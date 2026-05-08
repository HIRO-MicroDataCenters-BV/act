from typing import List, Optional

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field

from act.reproducibility.targets import ArchTarget


@dataclass
class PlanCheckResult:
    deterministic: bool
    hash_1: str
    hash_2: str
    diff: List[str] = field(default_factory=list)


def _flatten(obj, prefix: str = "") -> dict:
    flat: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flat.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        flat[prefix] = obj
    return flat


def _diff_paths(a: bytes, b: bytes, limit: int = 5) -> List[str]:
    fa = _flatten(json.loads(a))
    fb = _flatten(json.loads(b))
    paths: List[str] = []
    for path in sorted(set(fa) | set(fb)):
        if fa.get(path) != fb.get(path):
            paths.append(path)
            if len(paths) >= limit:
                break
    return paths


class PlanCheck:
    def __init__(self, target: Optional[ArchTarget] = None):
        self._target = target

    def run(self, program_path: str, schema_path) -> PlanCheckResult:
        schemas = [schema_path] if isinstance(schema_path, str) else list(schema_path)
        out_1 = self._capture_host(program_path, schemas)
        out_2 = self._capture_host(program_path, schemas)
        h1 = hashlib.sha256(out_1).hexdigest()
        h2 = hashlib.sha256(out_2).hexdigest()
        diff = [] if h1 == h2 else _diff_paths(out_1, out_2)
        return PlanCheckResult(deterministic=h1 == h2, hash_1=h1, hash_2=h2, diff=diff)

    def _capture_host(self, program_path: str, schemas: List[str]) -> bytes:
        cmd = [sys.executable, "-m", "act.reproducibility.capture", "--program", program_path, "--schema", *schemas]
        return subprocess.run(cmd, capture_output=True, check=True).stdout
