from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional

from pulumi import automation

from act.core.mock_generator import MockGenerator
from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

if TYPE_CHECKING:  # pragma: no cover
    pass


VOLATILE_KEYS: frozenset[str] = frozenset({
    "creationTimestamp",
    "resourceVersion",
    "uid",
    "generation",
    "selfLink",
    "managedFields",
    "lastTransitionTime",
    "startTime",
    "completionTime",
})

VOLATILE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"pid:\s*\d+", flags=re.IGNORECASE),
    re.compile(r"\b\d{10,}\b"),  # epoch-shaped numbers
    re.compile(r":\b[0-9]{5}\b"),  # ephemeral ports
)


RuntimeCheckStage = Literal[
    "substrate_unavailable",
    "spec_unsupported",
    "provision_failed",
    "pulumi_up_failed",
    "probe_failed",
    "timeout",
    "output_mismatch",
    "teardown_failed",
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


def _program_loader(program_path: str):
    import importlib.util
    import sys
    from pathlib import Path

    def load() -> None:
        path = Path(program_path)
        spec = importlib.util.spec_from_file_location("_act_runtime_prog", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load program at {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

    return load


def run_pulumi_against(
    target: ProvisionedTarget,
    program_path: str,
    backend_dir: str,
    project_name: str = "act-runtime-check",
) -> PulumiUpOutcome:
    stack_name = f"act-{uuid.uuid4().hex[:8]}"
    env_vars = {
        "PULUMI_BACKEND_URL": f"file://{backend_dir}",
        "PULUMI_CONFIG_PASSPHRASE": "",
    }
    workspace_opts = automation.LocalWorkspaceOptions(env_vars=env_vars)

    stack = automation.create_or_select_stack(
        stack_name=stack_name,
        project_name=project_name,
        program=_program_loader(program_path),
        opts=workspace_opts,
    )

    if target.kind == "kubeconfig":
        stack.set_config("kubernetes:kubeconfig", automation.ConfigValue(value=target.endpoint))

    failure: Optional[RuntimeCheckFailure] = None
    outputs: dict = {}
    try:
        up_result = stack.up()
        outputs = {k: getattr(v, "value", v) for k, v in (up_result.outputs or {}).items()}
    except Exception as exc:
        failure = RuntimeCheckFailure(stage="pulumi_up_failed", detail=str(exc))
    finally:
        try:
            stack.destroy()
        except Exception as exc:
            if failure is None:
                failure = RuntimeCheckFailure(stage="teardown_failed", detail=str(exc))

    return PulumiUpOutcome(outputs=outputs, failure=failure)


_ARCH_NORMALISE = {
    "amd64": "x86_64-linux",
    "x86_64": "x86_64-linux",
    "x86-64": "x86_64-linux",
    "arm64": "aarch64-linux",
    "aarch64": "aarch64-linux",
    "riscv64": "riscv64-linux",
}


def _normalise_arch(raw: str) -> str:
    return _ARCH_NORMALISE.get(raw.strip().lower(), f"{raw}-linux")


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
    return "cxl" in json.dumps(outputs, default=str).lower()


def probe_k8s(kubeconfig: str, timeout: int = 60) -> dict:
    result = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, "get", "pods", "--all-namespaces", "-o", "json"],
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    return json.loads(result.stdout)


def _strip_volatile_values(value: str) -> str:
    for pattern in VOLATILE_VALUE_PATTERNS:
        value = pattern.sub("", value)
    return value


def normalise_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: normalise_output(v)
            for k, v in value.items()
            if k not in VOLATILE_KEYS
        }
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
    def __init__(self, substrates: list[Substrate]):
        self._substrates = substrates

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

    def run(self, program_path: str, schema_path, backend_dir: Optional[str] = None) -> RuntimeCheckResult:
        schemas = [schema_path] if isinstance(schema_path, str) else list(schema_path)
        start = time.monotonic_ns()

        mg = MockGenerator(schemas)
        plan = mg.run_with_mocks(program_path)
        spec = extract_target_spec(plan, mg)

        substrate, pick_failure = self._pick_substrate(spec)
        if substrate is None or pick_failure is not None:
            return RuntimeCheckResult(
                passed=False,
                substrate=substrate.name if substrate else "none",
                spec=spec,
                failures=[pick_failure] if pick_failure else [],
                capture_duration_ms=int((time.monotonic_ns() - start) // 1_000_000),
            )

        backend_root = backend_dir or tempfile.mkdtemp(prefix="act-pulumi-state-")
        failures: list[RuntimeCheckFailure] = []
        hashes: list[str] = []
        last_normalised: list[Any] = []

        provisioned: Optional[ProvisionedTarget] = None
        try:
            provisioned = substrate.provision(spec)

            for run_index in range(2):
                outcome = run_pulumi_against(
                    target=provisioned,
                    program_path=program_path,
                    backend_dir=backend_root,
                )
                if outcome.failure is not None:
                    failures.append(outcome.failure)
                    break

                try:
                    probed = probe_k8s(provisioned.endpoint)
                except subprocess.SubprocessError as exc:
                    failures.append(RuntimeCheckFailure(stage="probe_failed", detail=str(exc)))
                    break

                normalised = normalise_output(probed)
                last_normalised.append(normalised)
                hashes.append(hash_output(probed))

            if len(hashes) == 2:
                if hashes[0] != hashes[1]:
                    failures.append(
                        RuntimeCheckFailure(
                            stage="output_mismatch",
                            detail="probe output hashes differ between runs",
                        )
                    )
        except Exception as exc:
            failures.append(RuntimeCheckFailure(stage="provision_failed", detail=str(exc)))
        finally:
            if provisioned is not None:
                try:
                    provisioned.teardown()
                except Exception as exc:
                    failures.append(RuntimeCheckFailure(stage="teardown_failed", detail=str(exc)))

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
