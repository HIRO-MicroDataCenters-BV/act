from __future__ import annotations

from typing import Any, Callable, Literal, Optional

import functools
import hashlib
import inspect
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
from act.reproducibility._skip_await import skip_await_transformation
from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

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
        # System-assigned network/node identity; reassigned each apply, so drop by key.
        "clusterIP",
        "clusterIPs",
        "podIP",
        "podIPs",
        "hostIP",
        "hostIPs",
        "bootID",
        "machineID",
        # Runtime status churn: reassigned/regenerated each apply even at steady state.
        "containerID",
        "imageID",
        "startedAt",
        "observedGeneration",
    }
)

VOLATILE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Anchored pid scrub for logs; broader value scrubbing is omitted so distinct
    # values never hash equal (which would mask real drift).
    re.compile(r"pid:\s*\d+", flags=re.IGNORECASE),
)


RuntimeCheckStage = Literal[
    "internal_error",
    "nothing_observed",
    "output_mismatch",
    "probe_failed",
    "provision_failed",
    "pulumi_up_failed",
    "spec_unsupported",
    "substrate_unavailable",
    "teardown_failed",
    "timeout",
]

# Stages that mean "could not verify" (missing tooling, unsupported spec, nothing deployed, slow
# emulation) rather than "not reproducible" — they must not escalate the gate's exit code.
SKIP_STAGES: frozenset[str] = frozenset({"substrate_unavailable", "spec_unsupported", "nothing_observed", "timeout"})

# Compare depth: we hash what the cluster ACCEPTED, not a running workload. See the module note.
COMPARE_DEPTH = "deployment-accepted"

# Features whose target can't be truly emulated: verified via a proxy (gpu = scheduling contract,
# fpga = logic sim), and cxl is emulated but experimental. Drives the honest `mode`/`verified`.
_SIMULATED_FEATURES: frozenset[str] = frozenset({"gpu", "fpga"})
_EXPERIMENTAL_FEATURES: frozenset[str] = frozenset({"cxl"})


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
    mode: str = "emulation"
    depth: str = COMPARE_DEPTH
    verified: str = "unknown"


@dataclass
class PulumiUpOutcome:
    outputs: dict
    failure: Optional[RuntimeCheckFailure] = None
    probed: Optional[dict] = None


# Wrapper __main__ that registers the skipAwait transform (stamps `pulumi.com/skipAwait`
# on every k8s resource so `pulumi up` returns on acceptance), then runs the user program.
# We compare the accepted deployment, not a running workload, so waiting for readiness only
# slows the check (and stalls it under QEMU); the annotation is the provider's own opt-out.
# The transform's source is inlined (not imported) so the Pulumi program subprocess loads no
# `act` package; `from __future__ import annotations` keeps its type hints string-only.
_SKIP_AWAIT_WRAPPER = (
    "from __future__ import annotations\n\n"
    "import runpy\n"
    "from pathlib import Path\n\n"
    "import pulumi\n"
    "from pulumi.runtime import register_stack_transformation\n\n\n"
    + inspect.getsource(skip_await_transformation)
    + "\n\nregister_stack_transformation(skip_await_transformation)\n"
    + 'runpy.run_path(str(Path(__file__).with_name("_act_program.py")), run_name="__main__")\n'
)


def run_pulumi_against(
    target: ProvisionedTarget,
    program_path: str,
    backend_dir: str,
    project_name: str = "act-runtime-check",
    probe_fn: Optional[Callable[..., dict]] = None,
    skip_await: bool = True,
) -> PulumiUpOutcome:
    """Run `pulumi up` against the provisioned target, then `destroy`.

    LocalWorkspace project mode (subprocess-per-up), not inline programs:
    inline mode races Pulumi's grpc engine cleanup against real clusters
    ("Event loop stopped before Future completed"). Project mode dodges it
    at the cost of one temp dir + file copy per run.

    With `skip_await` (default), the program runs behind a wrapper that disables
    the k8s provider's readiness await, so `up` returns on acceptance — the
    deployment-accepted comparison never waits for the workload to run.

    `probe_fn` runs between `up` and `destroy` (into outcome.probed) so
    workloads whose state only exists while the stack is up (Jobs,
    short-lived Pods) are still readable.
    """
    stack_name = f"act-{uuid.uuid4().hex[:8]}"
    work_dir = Path(tempfile.mkdtemp(prefix="act-pulumi-prog-"))

    try:
        # Point Pulumi at the active venv (sys.prefix). Pulumi runs
        # `python -m pip list` to discover providers, so the venv needs pip;
        # uv-managed venvs need `uv pip install pip` once.
        (work_dir / "Pulumi.yaml").write_text(
            "name: " + project_name + "\n"
            "runtime:\n"
            "  name: python\n"
            "  options:\n"
            "    virtualenv: " + sys.prefix + "\n"
            "description: act runtime check\n"
        )
        if skip_await:
            shutil.copy(program_path, work_dir / "_act_program.py")
            (work_dir / "__main__.py").write_text(_SKIP_AWAIT_WRAPPER)
        else:
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
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            # Slow emulation, not a reproducibility violation — a skip stage, not red.
            failure = RuntimeCheckFailure(stage="timeout", detail=str(exc))
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


_ARCH_LABEL = "kubernetes.io/arch"


def _arch_from_pod_spec(pod_spec: dict) -> str | None:
    """Read the target arch from a pod spec's nodeSelector or required nodeAffinity."""
    node_selector = pod_spec.get("nodeSelector")
    if isinstance(node_selector, dict):
        raw = node_selector.get(_ARCH_LABEL)
        if isinstance(raw, str) and raw:
            return _normalise_arch(raw)
    affinity = pod_spec.get("affinity")
    node_affinity = affinity.get("nodeAffinity") if isinstance(affinity, dict) else None
    required = (
        node_affinity.get("requiredDuringSchedulingIgnoredDuringExecution") if isinstance(node_affinity, dict) else None
    )
    terms = required.get("nodeSelectorTerms") if isinstance(required, dict) else None
    for term in terms if isinstance(terms, list) else []:
        for expr in (term.get("matchExpressions") or []) if isinstance(term, dict) else []:
            if isinstance(expr, dict) and expr.get("key") == _ARCH_LABEL and expr.get("operator") == "In":
                values = expr.get("values")
                if isinstance(values, list) and values and isinstance(values[0], str):
                    return _normalise_arch(values[0])
    return None


def _resource_arch(outputs: dict) -> str | None:
    """Detect the declared target arch across pod-bearing kinds and bare Pods.

    Controllers (Deployment/StatefulSet/DaemonSet/Job/ReplicaSet) carry the pod spec under
    spec.template.spec; a bare Pod carries it directly under spec."""
    spec = outputs.get("spec")
    if not isinstance(spec, dict):
        return None
    template = spec.get("template")
    if isinstance(template, dict) and isinstance(template.get("spec"), dict):
        pod_spec = template["spec"]
    else:
        pod_spec = spec
    return _arch_from_pod_spec(pod_spec) if isinstance(pod_spec, dict) else None


# Canonical hardware markers per accelerator feature (Extended Resource request or node label).
# Anchored to these keys so we don't match incidental substrings in image names or free text.
_FEATURE_MARKERS: dict[str, tuple[str, ...]] = {
    "cxl": ("hardware.cape/cxl", "cape.eu/cxl"),
    "gpu": ("hardware.cape/gpu", "nvidia.com/gpu"),
    "fpga": ("hardware.cape/fpga", "cape.eu/fpga"),
}


def _has_dict_key(obj: Any, keys: frozenset[str]) -> bool:
    """True if any nested dict has one of `keys` as a key."""
    if isinstance(obj, dict):
        if not keys.isdisjoint(obj.keys()):
            return True
        return any(_has_dict_key(v, keys) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_dict_key(v, keys) for v in obj)
    return False


def _mentions_feature(outputs: dict, markers: tuple[str, ...]) -> bool:
    # Markers are Extended-Resource request or node-label KEYS; match them as keys, not as a
    # substring of the serialised outputs (which would false-positive on an image name or value).
    return _has_dict_key(outputs, frozenset(markers))


# System-managed objects in the `default` namespace, not user-deployed.
# Filtered from probe results to avoid timing-dependent diffs (e.g.
# `kube-root-ca.crt` is created shortly after the namespace exists, so an
# early probe sees one fewer item).
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
    """Accept either a ProvisionedTarget or a raw kubeconfig path string."""
    endpoint = getattr(target, "endpoint", None)
    return endpoint if endpoint is not None else target


_PROBE_KINDS = "pods,services,deployments,statefulsets,daemonsets,configmaps,secrets,jobs,cronjobs"


def _is_derived(item: dict) -> bool:
    """True for cluster-generated children (e.g. a Deployment's Pods): their health is
    reflected in the parent's readiness, and their specs carry injected non-determinism
    (generated names, per-Pod service-account token volumes)."""
    return bool((item.get("metadata") or {}).get("ownerReferences"))


def _kubectl_items(kubeconfig: str, kinds: str, namespace: str, timeout: int = 15) -> list:
    result = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, "get", kinds, "-n", namespace, "-o", "json"],
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    return json.loads(result.stdout).get("items", [])


def _project_resource(item: dict) -> dict:
    """The deterministic 'deployment outcome' we compare across runs: kind, namespace, user
    labels, and the cluster-accepted desired state (spec/data/binaryData). Names are excluded
    on purpose — physical names are generated (Pulumi autonaming, k8s suffixes), and
    program-level name determinism is Rung 1's (plan-hash) job. Annotations are excluded (they
    churn: last-applied-configuration, revision counters). Runtime status (readiness, IPs,
    containerIDs, timestamps) is also excluded: under skipAwait we compare what the cluster
    accepted, not whether the workload has finished coming up (which races between runs)."""
    metadata = item.get("metadata") or {}
    projected = {
        "kind": item.get("kind"),
        "namespace": metadata.get("namespace"),
    }
    labels = metadata.get("labels")
    if labels:
        projected["labels"] = labels
    # binaryData carries a ConfigMap's non-UTF-8 payload alongside string `data`.
    for key in ("spec", "data", "binaryData"):
        if key in item:
            projected[key] = item[key]
    return projected


def probe_k8s(target, namespace: str = "default", timeout: int = 60) -> dict:
    """Capture the reproducible deployment outcome of user resources in the namespace.

    Projects each user-declared object to (kind, namespace, accepted spec/data) — the
    deterministic "same deployment" signal — rather than the full, churn-heavy cluster dump.
    System-managed objects and cluster-derived children (Pods owned by a controller) are
    filtered out. Runs under skipAwait, so it captures the accepted deployment immediately
    after `up` without waiting for the workload to run. Accepts a `ProvisionedTarget` or
    kubeconfig path.
    """
    kubeconfig = _kubeconfig_path(target)
    resources = [
        _project_resource(i)
        for i in _kubectl_items(kubeconfig, _PROBE_KINDS, namespace, timeout)
        if not _is_system_managed(i) and not _is_derived(i)
    ]
    # Sort by the normalised form: a volatile field (e.g. a reassigned bootID) must not be able
    # to flip item order between runs and cause a false mismatch once it's stripped for hashing.
    resources.sort(key=lambda r: json.dumps(normalise_output(r), sort_keys=True, default=str))
    return {"resources": resources}


# Cluster-system pod prefixes (k3s defaults), skipped when capturing
# workload logs: their output drifts between probes (timestamps, readiness
# counters) and would mask user-workload determinism.
_SYSTEM_POD_PREFIXES: tuple[str, ...] = (
    "coredns-",
    "local-path-provisioner-",
    "metrics-server-",
    "traefik-",
    "svclb-",
    "helm-install-",
)

# Trailing random suffix Jobs/Deployments append to Pod names
# (`iverilog-boot-flow-7fkx2` -> `iverilog-boot-flow`); stripped so
# workload-logs keys are deterministic across runs.
_POD_NAME_SUFFIX = re.compile(r"-[a-z0-9]{5,10}(?:-[a-z0-9]{5,10})?$")


def _strip_pod_suffix(name: str) -> str:
    return _POD_NAME_SUFFIX.sub("", name)


def _wait_for_jobs(kubeconfig: str, namespace: str, timeout: int) -> None:
    """Wait until every Job in the namespace has succeeded or failed (no-op if none)."""
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

    Adds an `_act_workload_logs` key: stable pod-name prefix -> stdout/stderr.
    For FPGA/CXL boot-flow workloads where the deterministic value is the
    simulator's output, not just the manifests. Accepts a `ProvisionedTarget`
    or raw kubeconfig path.
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
    """Generic volatile-field scrub applied before hashing (drops volatile keys, scrubs pid values)."""
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


def _is_empty_probe(probe: Any) -> bool:
    """True when a probe observed no user resources or logs (nothing to compare)."""
    if not isinstance(probe, dict):
        return not probe
    return not probe.get("resources") and not probe.get("_act_workload_logs")


def _spec_mode(spec: TargetSpec) -> tuple[str, bool]:
    """(mode, experimental) for a spec: gpu/fpga are simulated proxies; cxl is emulated but
    experimental; everything else is real emulation."""
    if any(f in _SIMULATED_FEATURES for f in spec.features):
        return "simulation", True
    if any(f in _EXPERIMENTAL_FEATURES for f in spec.features):
        return "emulation", True
    return "emulation", False


def _verified_label(passed: bool, failures: list, experimental: bool) -> str:
    """Honest per-target status: a skip/experimental/proxy run can never read as a real green."""
    if any(f.stage in SKIP_STAGES for f in failures):
        return "skipped"
    if not passed:
        return "failed"
    return "experimental" if experimental else "verified"


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
            for feature, markers in _FEATURE_MARKERS.items():
                if feature not in features and _mentions_feature(outputs, markers):
                    features.append(feature)

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
        probe_fn: Optional[Callable[..., dict]] = None,
        namespace: str = "default",
        probe_timeout: int = 60,
    ):
        self._substrates = substrates
        # Bind namespace/timeout onto the default probe (run_pulumi_against invokes the
        # probe with only `target`). A caller-supplied probe_fn is passed through
        # unchanged so a custom (target)-only probe keeps working.
        self._probe_fn = (
            functools.partial(probe_k8s, namespace=namespace, timeout=probe_timeout) if probe_fn is None else probe_fn
        )

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

        mode, experimental = _spec_mode(spec)

        substrate, pick_failure = self._pick_substrate(spec)
        if substrate is None or pick_failure is not None:
            pick_failures = [pick_failure] if pick_failure else []
            return RuntimeCheckResult(
                passed=False,
                substrate=substrate.name if substrate else "none",
                spec=spec,
                failures=pick_failures,
                capture_duration_ms=int((time.monotonic_ns() - start) // 1_000_000),
                mode=mode,
                verified=_verified_label(False, pick_failures, experimental),
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

        try:
            # A fresh target per run: run 2 must agree with run 1 independently, not inherit
            # run 1's cluster residue (which would make a PASS vacuous and misreport leftovers
            # as pulumi_up_failed). Each iteration provisions, deploys, probes, and tears down.
            for _ in range(2):
                provisioned: Optional[ProvisionedTarget] = None
                try:
                    try:
                        provisioned = substrate.provision(spec)
                    except (TimeoutError, subprocess.TimeoutExpired) as exc:
                        # The emulated cluster didn't boot in time (slow arch under QEMU),
                        # not a reproducibility violation — a skip stage, not red.
                        failures.append(RuntimeCheckFailure(stage="timeout", detail=str(exc)))
                        break
                    except Exception as exc:
                        failures.append(RuntimeCheckFailure(stage="provision_failed", detail=str(exc)))
                        break
                    if provisioned is None:
                        # provision() returned None without raising: substrate contract
                        # violation. Surface it so the run isn't silently empty.
                        failures.append(
                            RuntimeCheckFailure(
                                stage="provision_failed",
                                detail="substrate.provision returned None",
                            )
                        )
                        break

                    outcome = run_pulumi_against(
                        target=provisioned,
                        program_path=program_path,
                        backend_dir=backend_root,
                        probe_fn=self._probe_fn,
                    )
                    if outcome.failure is not None:
                        failures.append(outcome.failure)
                        break

                    # probe_fn ran inside run_pulumi_against (between up and destroy);
                    # outcome.probed is the captured dict here.
                    probed = outcome.probed or {}
                    last_normalised.append(normalise_output(probed))
                    hashes.append(hash_output(probed))
                finally:
                    if provisioned is not None:
                        try:
                            provisioned.teardown()
                        except Exception as exc:
                            failures.append(RuntimeCheckFailure(stage="teardown_failed", detail=str(exc)))

            if len(hashes) == 2 and hashes[0] != hashes[1]:
                failures.append(
                    RuntimeCheckFailure(
                        stage="output_mismatch",
                        detail="probe output hashes differ between runs",
                    )
                )
            elif len(last_normalised) == 2 and all(_is_empty_probe(p) for p in last_normalised):
                # Matching but empty probes verify nothing; don't report reproducible.
                failures.append(
                    RuntimeCheckFailure(
                        stage="nothing_observed",
                        detail="no user resources observed; runtime reproducibility could not be verified",
                    )
                )
        except Exception as exc:
            failures.append(RuntimeCheckFailure(stage="internal_error", detail=str(exc)))
        finally:
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
            mode=mode,
            verified=_verified_label(passed, failures, experimental),
        )
