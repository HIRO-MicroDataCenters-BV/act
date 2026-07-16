"""Resolve provider schemas for a program when --schema is omitted.

AST-scans the entry point's imports for ``pulumi_*`` packages, maps each to a
Pulumi plugin, and fetches its schema via ``pulumi package get-schema``, cached
by provider and version. CAPE has no get-schema-able plugin in a stock checkout,
so it resolves from the schema bundled with ACT and prefers it over a fetch.
Best-effort: any failure raises :class:`SchemaResolveError` with a one-line,
actionable message (pass --schema; see 'act doctor')."""

from __future__ import annotations

from typing import Iterable, Optional

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from act.core.mock_generator import MockGenerator

# Overridable in tests via monkeypatch; defaults to the user cache.
_CACHE_DIR = Path(os.path.expanduser("~/.cache/act/schemas"))
_BUNDLED_DIR = Path(__file__).resolve().parent / "schemas"
# Plugins with no get-schema-able binary in a stock checkout -> use the bundled schema.
_BUNDLED: dict[str, str] = {"cape": "cape.json"}
_GET_SCHEMA_TIMEOUT_S = 120


class SchemaResolveError(Exception):
    """User-facing schema-resolution failure; printed as one line, no traceback."""


def _import_roots(program_path: str) -> set[str]:
    entry = MockGenerator._entry_point(program_path)
    try:
        tree = ast.parse(Path(entry).read_text())
    except SyntaxError as exc:
        raise SchemaResolveError(
            f"could not parse '{program_path}' to detect providers ({exc.msg}); pass --schema explicitly."
        )
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    return roots


def detect_plugins(program_path: str) -> list[str]:
    """Pulumi plugin names imported by the program (pulumi_aws_native -> aws-native)."""
    return sorted(
        root[len("pulumi_") :].replace("_", "-") for root in _import_roots(program_path) if root.startswith("pulumi_")
    )


def _bundled_schema(plugin: str) -> Optional[str]:
    if plugin in _BUNDLED:
        path = _BUNDLED_DIR / _BUNDLED[plugin]
        if path.is_file():
            return str(path)
    return None


def _cached_schema(plugin: str) -> Optional[str]:
    if not _CACHE_DIR.is_dir():
        return None
    hits = sorted(_CACHE_DIR.glob(f"{plugin}-*.json"))
    return str(hits[-1]) if hits else None


def _cache_write(plugin: str, version: str, content: str) -> str:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CACHE_DIR / f"{plugin}-{version}.json"
    fd, tmp = tempfile.mkstemp(dir=_CACHE_DIR, prefix=f".{plugin}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp, target)  # atomic
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return str(target)


def _fetch_schema(plugin: str) -> str:
    if shutil.which("pulumi") is None:
        raise SchemaResolveError(
            f"cannot resolve a schema for '{plugin}': the pulumi CLI is not on PATH. "
            "Pass --schema explicitly, or see 'act doctor'."
        )
    print(f"resolving schema for '{plugin}' via pulumi...", file=sys.stderr)
    try:
        proc = subprocess.run(
            ["pulumi", "package", "get-schema", plugin],
            capture_output=True,
            text=True,
            timeout=_GET_SCHEMA_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchemaResolveError(
            f"cannot resolve a schema for '{plugin}': {exc}. Pass --schema explicitly, or see 'act doctor'."
        )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = f" ({detail[-1]})" if detail else ""
        raise SchemaResolveError(
            f"cannot resolve a schema for '{plugin}': pulumi get-schema failed{tail}. "
            "Pass --schema explicitly, or see 'act doctor'."
        )
    try:
        version = str(json.loads(proc.stdout).get("version") or "latest")
    except (ValueError, TypeError):
        raise SchemaResolveError(
            f"cannot resolve a schema for '{plugin}': pulumi returned invalid JSON. Pass --schema explicitly."
        )
    return _cache_write(plugin, version, proc.stdout)


def _resolve_one(plugin: str) -> str:
    # Prefer the bundled schema, then a cached fetch, then a live fetch.
    return _bundled_schema(plugin) or _cached_schema(plugin) or _fetch_schema(plugin)


def resolve_schemas(program_path: str, explicit_schemas: Optional[Iterable[str]]) -> list[str]:
    """Schema paths for a program. An explicit --schema is a full override; otherwise
    resolve from the program's ``pulumi_*`` imports (empty list if it imports none)."""
    if explicit_schemas:
        return list(explicit_schemas)
    return [_resolve_one(plugin) for plugin in detect_plugins(program_path)]
