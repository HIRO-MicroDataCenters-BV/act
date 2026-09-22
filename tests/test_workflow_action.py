"""The workflow action wraps the CLI gate; its handler must report the same verdicts."""

import importlib.util
from pathlib import Path

import pytest

_HANDLER = Path(__file__).resolve().parent.parent / "workflow" / "act-check" / "ryax_handler.py"


@pytest.fixture(scope="module")
def handle():
    spec = importlib.util.spec_from_file_location("act_check_handler", _HANDLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handle


def test_valid_program_passes(handle, cape_fixtures):
    result = handle({"program": str(cape_fixtures / "path_a_valid.py")})
    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert result["report"].startswith("PASS")
    assert Path(result["artefact"]).is_file()


def test_invalid_program_fails_with_violations(handle, cape_fixtures):
    result = handle({"program": str(cape_fixtures / "path_a_invalid.py")})
    assert result["passed"] is False
    assert result["exit_code"] == 1
    assert result["report"].count("[HIGH]") == 2


def test_program_source_text_is_accepted(handle, cape_fixtures):
    result = handle({"program_source": (cape_fixtures / "path_a_invalid.py").read_text()})
    assert result["exit_code"] == 1


def test_missing_program_is_an_error(handle):
    with pytest.raises(ValueError):
        handle({})


def test_kubernetes_program_uses_bundled_schema_and_checkov(handle):
    program = Path(__file__).resolve().parent / "fixtures" / "kubernetes" / "nginx_deployment_no_security.py"
    result = handle({"program": str(program), "rules": "checkov"})
    assert result["exit_code"] == 1
    assert "CKV" in result["report"]


def _capture_argv(handle, monkeypatch, inputs):
    """Run the handler with the ACT entry point stubbed, returning the argv it built."""
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setitem(handle.__globals__, "act_main", fake_main)
    handle(inputs)
    return seen["argv"]


def test_log_level_input_reaches_the_cli(handle, monkeypatch, cape_fixtures):
    """The engine's log panel is where the layers show, so the level must be passed through."""
    argv = _capture_argv(handle, monkeypatch, {"program": str(cape_fixtures / "path_a_valid.py"), "log_level": "DEBUG"})
    assert argv[argv.index("--log-level") + 1] == "DEBUG"


def test_log_level_defaults_to_info(handle, monkeypatch, cape_fixtures):
    argv = _capture_argv(handle, monkeypatch, {"program": str(cape_fixtures / "path_a_valid.py")})
    assert argv[argv.index("--log-level") + 1] == "INFO"
