"""Schema auto-resolution: import detection, bundled CAPE, cache, and fetch.

Every fetch path is mocked; no test touches the network or the pulumi CLI.
"""

from types import SimpleNamespace

from pathlib import Path

import pytest

from act import schema_resolver

CAPE_PROGRAM = "tests/fixtures/cape/path_a_valid.py"
MULTI_PROGRAM = "tests/fixtures/multi_provider/program.py"


def _prog(tmp_path, body):
    p = tmp_path / "p.py"
    p.write_text(body)
    return str(p)


def test_detect_plugins_cape():
    assert schema_resolver.detect_plugins(CAPE_PROGRAM) == ["cape"]


def test_detect_plugins_multi_provider():
    assert schema_resolver.detect_plugins(MULTI_PROGRAM) == ["cape", "random"]


def test_detect_plugins_maps_underscores(tmp_path):
    assert schema_resolver.detect_plugins(_prog(tmp_path, "import pulumi_aws_native\n")) == ["aws-native"]


def test_explicit_schema_is_full_override():
    # Explicit schemas skip detection entirely (program is never read).
    assert schema_resolver.resolve_schemas("missing.py", ["a.json", "b.json"]) == ["a.json", "b.json"]


def test_cape_resolves_from_bundled():
    out = schema_resolver.resolve_schemas(CAPE_PROGRAM, None)
    assert len(out) == 1
    assert out[0].endswith("act/schemas/cape.json")
    assert Path(out[0]).is_file()


def test_no_pulumi_imports_resolves_empty(tmp_path):
    assert schema_resolver.resolve_schemas(_prog(tmp_path, "import os\nimport pulumi\n"), None) == []


def test_syntax_error_gives_clean_error(tmp_path):
    with pytest.raises(schema_resolver.SchemaResolveError):
        schema_resolver.resolve_schemas(_prog(tmp_path, "def (:\n"), None)


def test_fetch_missing_pulumi_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(schema_resolver.shutil, "which", lambda name: None)
    with pytest.raises(schema_resolver.SchemaResolveError) as exc:
        schema_resolver.resolve_schemas(_prog(tmp_path, "import pulumi_random\n"), None)
    assert "pulumi" in str(exc.value) and "--schema" in str(exc.value)


def test_fetch_failure_is_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(schema_resolver.shutil, "which", lambda name: "/usr/bin/pulumi")
    monkeypatch.setattr(
        schema_resolver.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="plugin not found"),
    )
    with pytest.raises(schema_resolver.SchemaResolveError) as exc:
        schema_resolver.resolve_schemas(_prog(tmp_path, "import pulumi_random\n"), None)
    assert "get-schema failed" in str(exc.value)


def test_fetch_success_caches_and_reuses(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(schema_resolver.shutil, "which", lambda name: "/usr/bin/pulumi")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='{"name":"random","version":"4.16.0","resources":{}}', stderr="")

    monkeypatch.setattr(schema_resolver.subprocess, "run", fake_run)
    prog = _prog(tmp_path, "import pulumi_random\n")

    out = schema_resolver.resolve_schemas(prog, None)
    assert len(out) == 1 and out[0].endswith("random-4.16.0.json")
    assert Path(out[0]).is_file()

    # A second run hits the cache; no further get-schema call.
    assert schema_resolver.resolve_schemas(prog, None) == out
    assert len(calls) == 1
