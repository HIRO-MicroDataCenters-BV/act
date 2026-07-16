"""Resolve provider schemas for a program when --schema is omitted.

AST-scans the entry point's imports for ``pulumi_*`` packages, maps each to a
Pulumi plugin, and resolves a schema for it from, in order:

1. a local ``<plugin>.json`` from a ``--schema-dir``, then the program's
   directory, a ``schemas/`` subdir beside it, or the working directory (this is
   how custom or in-house providers with no public plugin are covered);
2. a previously cached ``pulumi package get-schema`` under
   ``~/.cache/act/schemas/<plugin>/``;
3. a live ``pulumi package get-schema`` for standard providers, cached by version.

If none yields a schema, :class:`SchemaResolveError` is raised asking the user to
pass ``--schema`` explicitly, which always overrides resolution.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

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


def _search_dirs(program_path: str, extra_dirs: Sequence[str]) -> list[Path]:
    """Directories searched for a local <plugin>.json, in priority order."""
    prog_dir = Path(MockGenerator._entry_point(program_path)).resolve().parent
    candidates = [Path(d) for d in extra_dirs]
    candidates += [prog_dir, prog_dir / "schemas", Path.cwd(), Path.cwd() / "schemas"]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for d in candidates:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def _local_schema(plugin: str, program_path: str, extra_dirs: Sequence[str]) -> Optional[str]:
    for d in _search_dirs(program_path, extra_dirs):
        candidate = d / f"{plugin}.json"
        if candidate.is_file():
            return str(candidate)
    return None


def _cached_schema(plugin: str) -> Optional[str]:
    # Per-plugin subdirectory so a prefix-sharing name (e.g. aws vs aws-native)
    # can never cross-match another provider's cached schema.
    plugin_dir = _CACHE_DIR / plugin
    if not plugin_dir.is_dir():
        return None
    hits = sorted(plugin_dir.glob("*.json"))
    return str(hits[-1]) if hits else None


def _cache_write(plugin: str, version: str, content: str) -> str:
    plugin_dir = _CACHE_DIR / plugin
    plugin_dir.mkdir(parents=True, exist_ok=True)
    target = plugin_dir / f"{version}.json"
    fd, tmp = tempfile.mkstemp(dir=plugin_dir, prefix=f".{version}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp, target)  # atomic
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return str(target)


def _fetch_schema(plugin: str) -> Optional[str]:
    """Fetch via `pulumi package get-schema`, cache it, and return the path.

    Returns None when the pulumi CLI is absent (the caller turns that into a
    generic 'pass --schema' error). Raises SchemaResolveError, surfacing the
    underlying reason, when the CLI is present but cannot produce a schema.
    """
    if shutil.which("pulumi") is None:
        return None
    print(f"resolving schema for '{plugin}' via pulumi...", file=sys.stderr)
    hint = f"Pass --schema <path>, put '{plugin}.json' in a schemas/ directory, or use --schema-dir."
    try:
        proc = subprocess.run(
            ["pulumi", "package", "get-schema", plugin],
            capture_output=True,
            text=True,
            timeout=_GET_SCHEMA_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchemaResolveError(f"cannot resolve a schema for '{plugin}': {exc}. {hint}")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = f" ({detail[-1]})" if detail else ""
        raise SchemaResolveError(f"cannot resolve a schema for '{plugin}': pulumi get-schema failed{tail}. {hint}")
    try:
        version = str(json.loads(proc.stdout).get("version") or "latest")
    except (ValueError, TypeError):
        raise SchemaResolveError(f"cannot resolve a schema for '{plugin}': pulumi returned invalid JSON. {hint}")
    return _cache_write(plugin, version, proc.stdout)


def _resolve_one(plugin: str, program_path: str, extra_dirs: Sequence[str]) -> str:
    schema = _local_schema(plugin, program_path, extra_dirs) or _cached_schema(plugin) or _fetch_schema(plugin)
    if schema is None:  # pulumi CLI absent, and no local or cached schema
        raise SchemaResolveError(
            f"no schema found for provider '{plugin}': no local '{plugin}.json' on the search path, "
            "and the pulumi CLI is not available to fetch it. "
            f"Pass --schema <path>, put '{plugin}.json' in a schemas/ directory, or use --schema-dir."
        )
    return schema


def resolve_schemas(
    program_path: str,
    explicit_schemas: Optional[Iterable[str]],
    schema_dirs: Sequence[str] = (),
) -> list[str]:
    """Schema paths for a program. An explicit --schema is a full override; otherwise
    resolve one per provider imported by the program (empty if it imports none)."""
    if explicit_schemas:
        return list(explicit_schemas)
    return [_resolve_one(plugin, program_path, schema_dirs) for plugin in detect_plugins(program_path)]
