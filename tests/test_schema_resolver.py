"""Schema auto-resolution: import detection, local convention, cache, and fetch.

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


def test_no_pulumi_imports_resolves_empty(tmp_path):
    assert schema_resolver.resolve_schemas(_prog(tmp_path, "import os\nimport pulumi\n"), None) == []


def test_syntax_error_gives_clean_error(tmp_path):
    with pytest.raises(schema_resolver.SchemaResolveError):
        schema_resolver.resolve_schemas(_prog(tmp_path, "def (:\n"), None)


def test_local_schema_next_to_program(tmp_path):
    # A <plugin>.json beside the program is picked up with no fetch.
    (tmp_path / "random.json").write_text('{"name":"random","resources":{}}')
    out = schema_resolver.resolve_schemas(_prog(tmp_path, "import pulumi_random\n"), None)
    assert len(out) == 1 and Path(out[0]).resolve() == (tmp_path / "random.json").resolve()


def test_local_schema_in_schemas_subdir(tmp_path):
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "random.json").write_text('{"name":"random","resources":{}}')
    out = schema_resolver.resolve_schemas(_prog(tmp_path, "import pulumi_random\n"), None)
    assert len(out) == 1 and Path(out[0]).resolve() == (tmp_path / "schemas" / "random.json").resolve()


def test_schema_dir_override(tmp_path):
    custom = tmp_path / "vendor"
    custom.mkdir()
    (custom / "acme.json").write_text('{"name":"acme","resources":{}}')
    out = schema_resolver.resolve_schemas(_prog(tmp_path, "import pulumi_acme\n"), None, schema_dirs=[str(custom)])
    assert len(out) == 1 and Path(out[0]).resolve() == (custom / "acme.json").resolve()


def test_multi_provider_resolves_each_independently(tmp_path):
    # A program importing two providers (e.g. aws + kubernetes) resolves one schema each.
    (tmp_path / "aws.json").write_text('{"name":"aws","resources":{}}')
    (tmp_path / "kubernetes.json").write_text('{"name":"kubernetes","resources":{}}')
    prog = _prog(tmp_path, "import pulumi_aws\nfrom pulumi_kubernetes.apps.v1 import Deployment\n")
    out = schema_resolver.resolve_schemas(prog, None)
    assert sorted(Path(p).name for p in out) == ["aws.json", "kubernetes.json"]


def test_multi_provider_mixes_local_and_fetched(tmp_path, monkeypatch):
    # One provider resolves from a local file, the other is fetched and cached.
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(schema_resolver.shutil, "which", lambda name: "/usr/bin/pulumi")
    monkeypatch.setattr(
        schema_resolver.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(
            returncode=0, stdout='{"name":"aws","version":"6.0.0","resources":{}}', stderr=""
        ),
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "kubernetes.json").write_text('{"name":"kubernetes","resources":{}}')
    prog = _prog(tmp_path, "import pulumi_aws\nimport pulumi_kubernetes\n")
    out = schema_resolver.resolve_schemas(prog, None)
    assert len(out) == 2
    assert any(p.endswith("kubernetes.json") for p in out)  # local
    assert any(p.endswith("aws/6.0.0.json") for p in out)  # fetched + cached


def test_cape_fixture_resolves_locally():
    # The CAPE fixture ships a cape.json beside it; no special-casing in the resolver.
    out = schema_resolver.resolve_schemas(CAPE_PROGRAM, None)
    assert len(out) == 1 and out[0].endswith("tests/fixtures/cape/cape.json")
    assert Path(out[0]).is_file()


def test_unresolvable_provider_is_actionable(tmp_path, monkeypatch):
    # No local schema and no pulumi CLI -> a single actionable error.
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(schema_resolver.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(schema_resolver.SchemaResolveError) as exc:
        schema_resolver.resolve_schemas(_prog(tmp_path, "import pulumi_acme\n"), None)
    msg = str(exc.value)
    assert "acme" in msg and "--schema" in msg


def test_fetch_failure_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(schema_resolver.shutil, "which", lambda name: "/usr/bin/pulumi")
    monkeypatch.setattr(
        schema_resolver.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="plugin not found"),
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(schema_resolver.SchemaResolveError) as exc:
        schema_resolver.resolve_schemas(_prog(tmp_path, "import pulumi_random\n"), None)
    msg = str(exc.value)
    # The underlying reason is surfaced, and the message stays actionable.
    assert "get-schema failed" in msg and "plugin not found" in msg and "--schema" in msg


def test_cache_lookup_is_not_prefix_collided(tmp_path, monkeypatch):
    # A cached aws-native schema must never be returned when resolving aws.
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    native = tmp_path / "cache" / "aws-native"
    native.mkdir(parents=True)
    (native / "6.0.0.json").write_text('{"name":"aws-native"}')
    assert schema_resolver._cached_schema("aws") is None
    native_hit = schema_resolver._cached_schema("aws-native")
    assert native_hit is not None and native_hit.endswith("aws-native/6.0.0.json")


def test_cached_schema_picks_newest_by_semver(tmp_path, monkeypatch):
    # Lexical sort would wrongly pick 9.0.0 over 10.0.0, or latest over a real version.
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    plugin_dir = tmp_path / "cache" / "aws"
    plugin_dir.mkdir(parents=True)
    for name in ("9.0.0.json", "10.0.0.json", "latest.json"):
        (plugin_dir / name).write_text("{}")
    assert schema_resolver._cached_schema("aws").endswith("aws/10.0.0.json")


def test_cached_schema_falls_back_to_latest_when_only_option(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    plugin_dir = tmp_path / "cache" / "aws"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "latest.json").write_text("{}")
    assert schema_resolver._cached_schema("aws").endswith("aws/latest.json")


def test_fetch_success_caches_and_reuses(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_resolver, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(schema_resolver.shutil, "which", lambda name: "/usr/bin/pulumi")
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout='{"name":"random","version":"4.16.0","resources":{}}', stderr="")

    monkeypatch.setattr(schema_resolver.subprocess, "run", fake_run)
    prog = _prog(tmp_path, "import pulumi_random\n")

    out = schema_resolver.resolve_schemas(prog, None)
    assert len(out) == 1 and out[0].endswith("random/4.16.0.json")
    assert Path(out[0]).is_file()

    # A second run hits the cache; no further get-schema call.
    assert schema_resolver.resolve_schemas(prog, None) == out
    assert len(calls) == 1
