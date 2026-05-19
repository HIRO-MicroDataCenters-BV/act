from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from act.reproducibility.substrates.base import TargetSpec

if TYPE_CHECKING:
    from act.core.mock_generator import MockGenerator


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


def extract_target_spec(plan: dict, mg: "MockGenerator") -> TargetSpec:
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
