"""Writes one JSON file per ACT invocation under a configurable output directory.

Each file records the program path, schemas, plan check result, optional
deployment arch result, ACT/Pulumi/provider package versions, and an ISO
timestamp.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from act.reproducibility.deployment_arch import DeploymentArchResult
from act.reproducibility.plan_check import PlanCheckResult
from act.reproducibility.runtime_check import RuntimeCheckResult


def _safe_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _provider_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        meta = dist.metadata
        if not meta:
            continue
        name = meta["Name"]
        if name and name.startswith("pulumi_") and name != "pulumi":
            versions[name] = dist.version
    return dict(sorted(versions.items()))


@dataclass
class ReproducibilityArtefact:
    program_path: str
    schemas: list[str]
    plan_check: PlanCheckResult
    deployment_arch: Optional[DeploymentArchResult] = None
    runtime_check: Optional[RuntimeCheckResult] = None
    act_version: str = field(default_factory=lambda: _safe_version("act"))
    pulumi_version: str = field(default_factory=lambda: _safe_version("pulumi"))
    provider_versions: dict[str, str] = field(default_factory=_provider_versions)
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def write(artefact: ReproducibilityArtefact, output_dir: str) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = artefact.captured_at.replace(":", "-").replace("+00-00", "Z")
    path = os.path.join(output_dir, f"act_run_{timestamp}.json")
    payload = dataclasses.asdict(artefact)
    with open(path, "w") as f:
        f.write(json.dumps(payload, sort_keys=True, default=str, indent=2))
    return path
