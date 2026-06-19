from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Optional

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pulumi import automation

from act.core.mock_generator import MockGenerator
from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

if TYPE_CHECKING:  # pragma: no cover
    pass


VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "creationTimestamp",
        "resourceVersion",
        "uid",
        "generation",
        "selfLink",
        "managedFields",
        "lastTransitionTime",
        "startTime",
        "completionTime",
    }
)

VOLATILE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "pid: 12345" — process ids.
    re.compile(r"pid:\s*\d+", flags=re.IGNORECASE),
    # Unix epoch timestamps. Narrowed to "1[5-9]<8-11 digits>" so it scrubs
    # 2017-2055 second and millisecond timestamps without blanking long
    # numeric IDs that happen to be 10+ digits.
    re.compile(r"\b1[5-9]\d{8,11}\b"),
    # Ephemeral ports inside a URL-shaped host:port fragment. The fixed-width
    # lookbehind requires a host-like character immediately before the colon,
    # which skips JSON values like `"nodePort": 30001` while still scrubbing
    # `127.0.0.1:34567` and `host:34567` URLs.
    re.compile(r"(?<=[A-Za-z0-9.-]:)\b[0-9]{4,5}\b"),
)


RuntimeCheckStage = Literal[
    "internal_error",
    "output_mismatch",
    "probe_failed",
    "provision_failed",
    "pulumi_up_failed",
    "spec_unsupported",
    "substrate_unavailable",
    "teardown_failed",
    "timeout",
]


@dataclass
class RuntimeCheckFailure:
    stage: RuntimeCheckStage
    detail: str


@dataclass
class RuntimeCheckResult:
    passed: bool
    substrate: str
    spec: TargetSpec
    hash_1: str = ""
    hash_2: str = ""
    diff: list[str] = field(default_factory=list)
    failures: list[RuntimeCheckFailure] = field(default_factory=list)
    capture_duration_ms: int = 0


@dataclass
class PulumiUpOutcome:
    outputs: dict
    failure: Optional[RuntimeCheckFailure] = None
    probed: Optional[dict] = None


def run_pulumi_against(
    target: ProvisionedTarget,
    program_path: str,
    backend_dir: str,
    project_name: str = "act-runtime-check",
    probe_fn: Optional[callable] = None,  # type: ignore[valid-type]
) -> PulumiUpOutcome:
    """Run `pulumi up` against the provisioned target, then `destroy`.

    Uses LocalWorkspace project mode rather than inline programs: each pulumi
    invocation runs as a subprocess of the host Python. Inline mode
    (`create_or_select_stack(program=...)`) races with Pulumi's grpc engine
    cleanup against real clusters and trips an "Event loop stopped before
    Future completed" error even on a first up. Project mode dodges that
    entirely at the cost of one temp dir + one file copy per run.

    When `probe_fn` is provided it runs between `stack.up()` and
    `stack.destroy()` — important for workloads (Jobs, short-lived Pods)
    whose state only exists while the stack is up. The probe's return
    value lands in the `probed` field of the outcome.
    """
    stack_name = f"act-{uuid.uuid4().hex[:8]}"
    work_dir = Path(tempfile.mkdtemp(prefix="act-pulumi-prog-"))

    try:
        # Point Pulumi at the active venv (sys.prefix). Pulumi defaults to
        # `toolchain: pip` and runs `python -m pip list --format json` to
        # discover provider packages — that venv must therefore carry pip.
        # uv-managed venvs need `uv pip install pip` once to satisfy this.
        (work_dir / "Pulumi.yaml").write_text(
            "name: " + project_name + "\n"
            "runtime:\n"
            "  name: python\n"
            "  options:\n"
            "    virtualenv: " + sys.prefix + "\n"
            "description: act runtime check\n"
        )
        shutil.copy(program_path, work_dir / "__main__.py")

        env_vars = {
            "PULUMI_BACKEND_URL": f"file://{backend_dir}",
            "PULUMI_CONFIG_PASSPHRASE": "",
        }
        workspace_opts = automation.LocalWorkspaceOptions(env_vars=env_vars)

        stack = automation.create_or_select_stack(
            stack_name=stack_name,
            work_dir=str(work_dir),
            opts=workspace_opts,
        )

        if target.kind == "kubeconfig":
            stack.set_config("kubernetes:kubeconfig", automation.ConfigValue(value=target.endpoint))

        failure: Optional[RuntimeCheckFailure] = None
        outputs: dict = {}
        probed: Optional[dict] = None
        try:
            up_result = stack.up()
            outputs = {k: getattr(v, "value", v) for k, v in (up_result.outputs or {}).items()}
            if probe_fn is not None:
                try:
                    probed = probe_fn(target)
                except Exception as exc:
                    failure = RuntimeCheckFailure(stage="probe_failed", detail=str(exc))
        except Exception as exc:
            failure = RuntimeCheckFailure(stage="pulumi_up_failed", detail=str(exc))
        finally:
            try:
                stack.destroy()
            except Exception as exc:
                if failure is None:
                    failure = RuntimeCheckFailure(stage="teardown_failed", detail=str(exc))

        return PulumiUpOutcome(outputs=outputs, failure=failure, probed=probed)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


_ARCH_NORMALISE = {
    "amd64": "x86_64-linux",
    "x86_64": "x86_64-linux",
    "x86-64": "x86_64-linux",
    "arm64": "aarch64-linux",
    "aarch64": "aarch64-linux",
    "riscv64": "riscv64-linux",
}


def _normalise_arch(raw: str) -> str:
    cleaned = raw.strip().lower()
    if cleaned.endswith("-linux"):
        return cleaned
    return _ARCH_NORMALISE.get(cleaned, f"{cleaned}-linux")


def _resource_arch(outputs: dict) -> str | None:
    spec = outputs.get("spec") if isinstance(outputs.get("spec"), dict) else None
    if not spec:
        return None
    template = spec.get("template") if isinstance(spec.get("template"), dict) else None
    if not template:
        return None
    pod_spec = template.get("spec") if isinstance(template.get("spec"), dict) else None
    if not pod_spec:
        return None
    node_selector = pod_spec.get("nodeSelector")
    if isinstance(node_selector, dict):
        raw = node_selector.get("kubernetes.io/arch")
        if isinstance(raw, str) and raw:
            return _normalise_arch(raw)
    return None


def _mentions_cxl(outputs: dict) -> bool:
    """True if the resource declares a CXL hardware marker.

    Anchored to the canonical CXL labels/resource keys (`hardware.cape/cxl`
    and `cape.eu/cxl`) so it does not match incidental occurrences of "cxl"
    in image names, comments, or other free-text fields.
    """
    text = json.dumps(outputs, default=str)
    return "hardware.cape/cxl" in text or "cape.eu/cxl" in text


# System-managed objects that show up in the `default` namespace but
# aren't user-deployed. Filtered out of probe results so they don't
# create timing-dependent diffs between runs (e.g. `kube-root-ca.crt`
# is created by kube-controller-manager shortly after the namespace
# exists; a probe that lands before its creation sees one fewer item).
_SYSTEM_NAMESPACE_OBJECTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("Service", "kubernetes"),
        ("ConfigMap", "kube-root-ca.crt"),
        ("Endpoints", "kubernetes"),
        ("EndpointSlice", "kubernetes"),
    }
)


def _is_system_managed(item: dict) -> bool:
    name = item.get("metadata", {}).get("name", "")
    kind = item.get("kind", "")
    return (kind, name) in _SYSTEM_NAMESPACE_OBJECTS


def _kubeconfig_path(target) -> str:
    """Accept either a ProvisionedTarget or a raw kubeconfig path string.

    The probe functions historically took a raw string; passing the full
    `ProvisionedTarget` is cleaner (the signature no longer lies about
    what it depends on) and opens the door for future SSH/HTTP probes
    that need other fields on the target.
    """
    endpoint = getattr(target, "endpoint", None)
    return endpoint if endpoint is not None else target


def probe_k8s(target, namespace: str = "default", timeout: int = 60) -> dict:
    """Capture user-deployed state in the named namespace.

    Scoped to a single (non-system) namespace because `--all-namespaces`
    pulls in kube-system pods whose state (restartCount, container
    statuses, transient conditions) drifts between probes and would
    surface as spurious reproducibility violations. Capture covers the
    resource kinds a CAPE Pulumi program is most likely to deploy.
    System-managed objects present in the default namespace
    (`kubernetes` Service, `kube-root-ca.crt` ConfigMap) are filtered
    out — their lifecycle is driven by control-plane controllers and
    races with our probe timing.

    Accepts either a `ProvisionedTarget` or a raw kubeconfig path string.
    """
    kubeconfig = _kubeconfig_path(target)
    kinds = "pods,services,deployments,statefulsets,daemonsets,configmaps,secrets,jobs,cronjobs"
    result = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, "get", kinds, "-n", namespace, "-o", "json"],
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    payload = json.loads(result.stdout)
    items = payload.get("items", [])
    payload["items"] = [item for item in items if not _is_system_managed(item)]
    return payload


# Pod-name prefixes that identify cluster-system pods (k3s defaults). When
# capturing workload logs we skip these — their output drifts between
# probes (timestamps, readiness counters) and would mask user-workload
# determinism.
_SYSTEM_POD_PREFIXES: tuple[str, ...] = (
    "coredns-",
    "local-path-provisioner-",
    "metrics-server-",
    "traefik-",
    "svclb-",
    "helm-install-",
)

# Trailing random suffix that Jobs/Deployments append to Pod names.
# `iverilog-boot-flow-7fkx2` -> `iverilog-boot-flow`. Stripping these
# makes the workload-logs dict's keys deterministic across runs.
_POD_NAME_SUFFIX = re.compile(r"-[a-z0-9]{5,10}(?:-[a-z0-9]{5,10})?$")


def _strip_pod_suffix(name: str) -> str:
    return _POD_NAME_SUFFIX.sub("", name)


def _wait_for_jobs(kubeconfig: str, namespace: str, timeout: int) -> None:
    """Wait until every Job in the namespace has succeeded or failed.

    No-op when no Jobs are present.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["kubectl", "--kubeconfig", kubeconfig, "get", "jobs", "-n", namespace, "-o", "json"],
            capture_output=True,
            check=True,
            timeout=15,
        )
        items = json.loads(result.stdout).get("items", [])
        if not items:
            return
        all_done = True
        for job in items:
            status = job.get("status", {})
            succeeded = status.get("succeeded", 0)
            failed = status.get("failed", 0)
            if succeeded == 0 and failed == 0:
                all_done = False
                break
        if all_done:
            return
        time.sleep(2)
    raise TimeoutError(f"jobs in namespace {namespace!r} did not complete within {timeout}s")


def _capture_workload_logs(kubeconfig: str, namespace: str, timeout: int) -> dict:
    """Collect logs from non-system pods in the namespace, keyed by stable prefix."""
    pod_list = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, "get", "pods", "-n", namespace, "-o", "json"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    pods = json.loads(pod_list.stdout).get("items", [])
    logs: dict[str, str] = {}
    for pod in pods:
        name = pod.get("metadata", {}).get("name", "")
        if not name or name.startswith(_SYSTEM_POD_PREFIXES):
            continue
        result = subprocess.run(
            ["kubectl", "--kubeconfig", kubeconfig, "logs", name, "-n", namespace, "--all-containers=true"],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode == 0:
            logs[_strip_pod_suffix(name)] = result.stdout.decode()
    return logs


def probe_k8s_with_workload_logs(
    target,
    namespace: str = "default",
    timeout: int = 60,
) -> dict:
    """Like `probe_k8s` but also waits for Jobs and captures workload pod logs.

    Returns the same shape as `probe_k8s` with an extra `_act_workload_logs`
    key — a dict keyed by stable pod-name prefix (random suffix stripped)
    mapping to the pod's stdout/stderr. Used by FPGA / CXL boot-flow style
    workloads where the deterministic value is the simulator's output,
    not just the resource manifests.

    Accepts either a `ProvisionedTarget` or a raw kubeconfig path string.
    """
    kubeconfig = _kubeconfig_path(target)
    _wait_for_jobs(kubeconfig, namespace, timeout)
    state = probe_k8s(kubeconfig, namespace, timeout)
    state["_act_workload_logs"] = _capture_workload_logs(kubeconfig, namespace, timeout)
    return state


def _strip_volatile_values(value: str) -> str:
    for pattern in VOLATILE_VALUE_PATTERNS:
        value = pattern.sub("", value)
    return value


def normalise_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalise_output(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [normalise_output(v) for v in value]
    if isinstance(value, str):
        return _strip_volatile_values(value)
    return value


def hash_output(value: Any) -> str:
    normalised = normalise_output(value)
    canonical = json.dumps(normalised, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_target_spec(plan: dict, mg: MockGenerator) -> TargetSpec:
    arch = "x86_64-linux"
    orchestrator: str | None = None
    features: list[str] = []

    for resource_name, outputs in plan.items():
        token = mg.get_resource_type(resource_name)
        if not isinstance(token, str):
            continue
        if token.startswith("kubernetes:"):
            orchestrator = "k8s"
        if isinstance(outputs, dict):
            found_arch = _resource_arch(outputs)
            if found_arch is not None:
                arch = found_arch
            if _mentions_cxl(outputs) and "cxl" not in features:
                features.append("cxl")

    return TargetSpec(arch=arch, orchestrator=orchestrator, features=features)


def _diff_paths(a: Any, b: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            paths.extend(_diff_paths(a.get(key), b.get(key), f"{prefix}.{key}" if prefix else str(key)))
            if len(paths) >= 5:
                return paths[:5]
    elif a != b:
        paths.append(prefix or "<root>")
    return paths[:5]


class RuntimeCheck:
    def __init__(
        self,
        substrates: list[Substrate],
        probe_fn: Optional[callable] = None,  # type: ignore[valid-type]
    ):
        self._substrates = substrates
        self._probe_fn = probe_fn or probe_k8s

    def _pick_substrate(self, spec: TargetSpec) -> tuple[Optional[Substrate], Optional[RuntimeCheckFailure]]:
        unavailable: list[str] = []
        for sub in self._substrates:
            if not sub.matches(spec):
                continue
            if not sub.is_available():
                unavailable.append(sub.name)
                continue
            return sub, None

        if unavailable:
            return None, RuntimeCheckFailure(
                stage="substrate_unavailable",
                detail=f"matching substrates not available: {', '.join(unavailable)}",
            )
        return None, RuntimeCheckFailure(
            stage="spec_unsupported",
            detail=f"no substrate matches spec arch={spec.arch} orchestrator={spec.orchestrator}",
        )

    def run(
        self,
        program_path: str,
        schema_path,
        backend_dir: Optional[str] = None,
        arch_override: Optional[str] = None,
    ) -> RuntimeCheckResult:
        schemas = [schema_path] if isinstance(schema_path, str) else list(schema_path)
        start = time.monotonic_ns()

        mg = MockGenerator(schemas)
        plan = mg.run_with_mocks(program_path)
        spec = extract_target_spec(plan, mg)
        if arch_override is not None:
            spec = TargetSpec(
                arch=_normalise_arch(arch_override),
                orchestrator=spec.orchestrator,
                features=spec.features,
            )

        substrate, pick_failure = self._pick_substrate(spec)
        if substrate is None or pick_failure is not None:
            return RuntimeCheckResult(
                passed=False,
                substrate=substrate.name if substrate else "none",
                spec=spec,
                failures=[pick_failure] if pick_failure else [],
                capture_duration_ms=int((time.monotonic_ns() - start) // 1_000_000),
            )

        if backend_dir is None:
            backend_root = tempfile.mkdtemp(prefix="act-pulumi-state-")
            owns_backend_root = True
        else:
            backend_root = backend_dir
            owns_backend_root = False
        failures: list[RuntimeCheckFailure] = []
        hashes: list[str] = []
        last_normalised: list[Any] = []

        provisioned: Optional[ProvisionedTarget] = None
        try:
            try:
                provisioned = substrate.provision(spec)
            except Exception as exc:
                failures.append(RuntimeCheckFailure(stage="provision_failed", detail=str(exc)))

            if provisioned is not None:
                try:
                    for _run_index in range(2):
                        outcome = run_pulumi_against(
                            target=provisioned,
                            program_path=program_path,
                            backend_dir=backend_root,
                            probe_fn=self._probe_fn,
                        )
                        if outcome.failure is not None:
                            failures.append(outcome.failure)
                            break

                        # probe_fn runs inside run_pulumi_against (between up and
                        # destroy). If it raises, outcome.failure is set with
                        # stage=probe_failed and we break above. So if we reach
                        # here, outcome.probed is the captured dict.
                        probed = outcome.probed or {}
                        normalised = normalise_output(probed)
                        last_normalised.append(normalised)
                        hashes.append(hash_output(probed))

                    if len(hashes) == 2 and hashes[0] != hashes[1]:
                        failures.append(
                            RuntimeCheckFailure(
                                stage="output_mismatch",
                                detail="probe output hashes differ between runs",
                            )
                        )
                except Exception as exc:
                    failures.append(RuntimeCheckFailure(stage="internal_error", detail=str(exc)))
            elif not failures:
                # provision() returned None without raising — substrate contract
                # violation. Surface it so the run isn't silently empty.
                failures.append(
                    RuntimeCheckFailure(
                        stage="provision_failed",
                        detail="substrate.provision returned None",
                    )
                )
        finally:
            if provisioned is not None:
                try:
                    provisioned.teardown()
                except Exception as exc:
                    failures.append(RuntimeCheckFailure(stage="teardown_failed", detail=str(exc)))
            if owns_backend_root:
                shutil.rmtree(backend_root, ignore_errors=True)

        passed = not failures and len(hashes) == 2 and hashes[0] == hashes[1]
        diff: list[str] = []
        if len(last_normalised) == 2 and hashes[0] != hashes[1]:
            diff = _diff_paths(last_normalised[0], last_normalised[1])

        return RuntimeCheckResult(
            passed=passed,
            substrate=substrate.name,
            spec=spec,
            hash_1=hashes[0] if len(hashes) >= 1 else "",
            hash_2=hashes[1] if len(hashes) >= 2 else "",
            diff=diff,
            failures=failures,
            capture_duration_ms=int((time.monotonic_ns() - start) // 1_000_000),
        )
