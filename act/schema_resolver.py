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

from packaging.version import InvalidVersion, Version

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


def _version_key(stem: str) -> tuple:
    """Sort key so newer semver wins; unparseable names (e.g. 'latest') sort lowest."""
    try:
        return (1, Version(stem))
    except InvalidVersion:
        return (0, Version("0"))


def _cached_schema(plugin: str) -> Optional[str]:
    # Per-plugin subdirectory so a prefix-sharing name (e.g. aws vs aws-native)
    # can never cross-match another provider's cached schema.
    plugin_dir = _CACHE_DIR / plugin
    if not plugin_dir.is_dir():
        return None
    hits = list(plugin_dir.glob("*.json"))
    return str(max(hits, key=lambda p: _version_key(p.stem))) if hits else None


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


def _fetch_schema(plugin: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch via `pulumi package get-schema` and cache it.

    Returns (path, None) on success, or (None, reason) when the CLI is absent or
    cannot produce a schema.
    """
    if shutil.which("pulumi") is None:
        return None, "the pulumi CLI is not available to fetch it"
    print(f"resolving schema for '{plugin}' via pulumi...", file=sys.stderr)
    try:
        proc = subprocess.run(
            ["pulumi", "package", "get-schema", plugin],
            capture_output=True,
            text=True,
            timeout=_GET_SCHEMA_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"pulumi get-schema errored: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = f" ({detail[-1]})" if detail else ""
        return None, f"pulumi get-schema failed{tail}"
    try:
        version = str(json.loads(proc.stdout).get("version") or "latest")
    except (ValueError, TypeError):
        return None, "pulumi returned invalid JSON"
    return _cache_write(plugin, version, proc.stdout), None


def _resolve_one(
    plugin: str, program_path: str, extra_dirs: Sequence[str], allow_fetch: bool = False
) -> tuple[Optional[str], Optional[str]]:
    """Return (schema_path, None) on success, or (None, reason) if it can't be resolved."""
    schema = _local_schema(plugin, program_path, extra_dirs) or _cached_schema(plugin)
    if schema is not None:
        return schema, None
    # A network fetch downloads and runs the provider plugin binary named by an
    # untrusted program's imports, so it can be disabled for offline/hardened runs.
    if allow_fetch:
        return _fetch_schema(plugin)
    return None, "no local schema and fetch is disabled"


def _schema_provider_name(path: str) -> Optional[str]:
    """The provider name a schema file declares, or None if unreadable/unnamed."""
    try:
        with open(path) as f:
            return json.load(f).get("name")
    except (OSError, ValueError):
        return None


def _missing_schemas_error(missing: list[tuple[str, Optional[str]]]) -> SchemaResolveError:
    lines = "\n".join(f"  - {plugin}: {reason}" for plugin, reason in missing)
    return SchemaResolveError(
        f"no schema for provider(s):\n{lines}\n"
        "Pass --schema <path> for each, put '<plugin>.json' in a schemas/ directory, "
        "use --schema-dir, or allow fetch (drop --no-schema-fetch / set ACT_SCHEMA_FETCH=allow)."
    )


def resolve_schemas(
    program_path: str,
    explicit_schemas: Optional[Iterable[str]],
    schema_dirs: Sequence[str] = (),
    allow_fetch: bool = False,
) -> list[str]:
    """Schema paths for a program. Explicit --schema files are always included and take
    priority for the providers they declare; any imported provider not covered by one is
    resolved (local -> cached -> fetch when ``allow_fetch``). No --schema resolves them all.

    Every provider that still has no schema is collected and raised together, so the user
    sees the full set to supply rather than fixing them one run at a time."""
    explicit = list(explicit_schemas or [])
    covered = {name for name in (_schema_provider_name(p) for p in explicit) if name}
    resolved: list[str] = []
    missing: list[tuple[str, Optional[str]]] = []
    for plugin in detect_plugins(program_path):
        if plugin in covered:
            continue
        path, reason = _resolve_one(plugin, program_path, schema_dirs, allow_fetch)
        if path is not None:
            resolved.append(path)
        else:
            missing.append((plugin, reason))
    if missing:
        raise _missing_schemas_error(missing)
    return explicit + resolved
