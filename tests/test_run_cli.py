"""CLI shell tests: subcommand dispatch, info commands, and backward-compat.

All drive act.run.main directly, so they exercise the dispatch without any heavy
dependency (the info commands need no schema, the check paths use the CAPE fixture).
"""

import pytest

from act.run import main

CAPE_PROGRAM = "tests/fixtures/cape/path_a_valid.py"
CAPE_INVALID = "tests/fixtures/cape/path_a_invalid.py"
CAPE_SCHEMA = "tests/fixtures/cape/schema.json"


def test_bare_act_prints_help(capsys):
    code = main([])
    assert code == 0
    out = capsys.readouterr().out
    assert "usage: act <command>" in out
    assert "list-rules" in out


def test_help_flag_prints_help(capsys):
    assert main(["--help"]) == 0
    assert "usage: act <command>" in capsys.readouterr().out


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() != ""


def test_version_flag_alias(capsys):
    assert main(["--version"]) == 0
    first = capsys.readouterr().out.strip()
    assert main(["-V"]) == 0
    assert capsys.readouterr().out.strip() == first


def test_list_rules_without_schema(capsys):
    assert main(["list-rules"]) == 0
    out = capsys.readouterr().out
    assert "rule_no_exposed_instance" in out
    assert "rule_no_unprotected_ssh" in out


def test_list_providers_without_schema(capsys):
    assert main(["list-providers"]) == 0
    assert "cape" in capsys.readouterr().out


def test_check_subcommand_passes(capsys):
    assert main(["check", "--program", CAPE_PROGRAM, "--schema", CAPE_SCHEMA]) == 0
    assert "PASS" in capsys.readouterr().out


def test_default_command_backward_compat(capsys):
    # No subcommand: `act --program … --schema …` must still run the check.
    assert main(["--program", CAPE_INVALID, "--schema", CAPE_SCHEMA]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_check_missing_program_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", "--schema", CAPE_SCHEMA])
    assert exc.value.code == 2
    assert "required" in capsys.readouterr().err


def test_check_auto_resolves_cape_schema(capsys):
    # No --schema: CAPE resolves from the cape.json beside the program (local convention).
    assert main(["check", "--program", CAPE_PROGRAM]) == 0
    assert "PASS" in capsys.readouterr().out


def test_check_auto_resolves_multiple_providers(capsys):
    # A two-provider program (CAPE + random) auto-resolves both local schemas and
    # captures resources from each; identical to passing both via --schema.
    code = main(["check", "--program", "tests/fixtures/multi_provider/program.py"])
    out = capsys.readouterr().out
    assert code != 2  # ran to completion, not a pipeline error
    assert "2 resources" in out  # one from each provider


def test_check_nonexistent_program_is_clean(capsys):
    code = main(["check", "--program", "does_not_exist.py", "--schema", CAPE_SCHEMA])
    err = capsys.readouterr().err
    assert code == 2
    assert err.strip() == "[ERROR] program not found: does_not_exist.py"
    assert "Traceback" not in err


def test_check_nonexistent_schema_is_clean(capsys):
    code = main(["check", "--program", CAPE_PROGRAM, "--schema", "nope.json"])
    err = capsys.readouterr().err
    assert code == 2
    assert err.strip() == "[ERROR] schema not found: nope.json"
    assert "Traceback" not in err


def test_zero_resources_warns_and_fails_closed(capsys):
    code = main(["check", "--program", "tests/fixtures/no_resources.py", "--schema", CAPE_SCHEMA])
    out = capsys.readouterr().out
    assert code == 2  # nothing captured -> nothing validated
    assert "FAIL" in out
    assert "WARN  no resources captured" in out


def test_program_stdout_does_not_leak(capsys):
    # The program prints a marker; it must not pollute ACT's report.
    code = main(["check", "--program", "tests/fixtures/prints_to_stdout.py", "--schema", CAPE_SCHEMA])
    out = capsys.readouterr().out
    assert code == 2  # fixture declares no resources
    assert "LEAK_MARKER_SHOULD_NOT_APPEAR" not in out


def test_summary_prints_by_default(capsys):
    assert main(["check", "--program", CAPE_PROGRAM, "--schema", CAPE_SCHEMA]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "Summary:" in out
    assert "0 violations" in out


def test_summary_suppressed_by_quiet(capsys):
    assert main(["check", "--program", CAPE_PROGRAM, "--schema", CAPE_SCHEMA, "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "Summary:" not in out


def test_summary_on_failing_check(capsys):
    assert main(["--program", CAPE_INVALID, "--schema", CAPE_SCHEMA]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "Summary:" in out
    assert "violation" in out


def test_no_summary_on_input_error(capsys):
    assert main(["check", "--program", "nope.py", "--schema", CAPE_SCHEMA]) == 2
    assert "Summary:" not in capsys.readouterr().out


def test_doctor_runs_and_exits_zero(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "ACT preflight" in out
    assert "--check-deployment-runtime" in out
    assert "--acv-mode blocking" in out


def test_doctor_in_top_level_help(capsys):
    main([])
    assert "doctor" in capsys.readouterr().out


def test_doctor_flags_missing_checkov(capsys, monkeypatch):
    import act.doctor as doctor
    from act.config import ActConfig

    monkeypatch.setattr(doctor, "_checkov_installed", lambda: False)
    assert doctor.run(ActConfig.from_env({})) == 0
    out = capsys.readouterr().out
    assert "--rules checkov" in out and "needs checkov" in out


def test_doctor_reflects_acv_env(capsys):
    import re

    from act.config import ActConfig
    from act.doctor import run as doctor_run

    cfg = ActConfig.from_env({"ACT_ACV_MODEL": "m", "ACT_ACV_BASE_URL": "http://x"})
    assert doctor_run(cfg) == 0
    norm = re.sub(r" +", " ", capsys.readouterr().out)
    assert "ACT_ACV_MODEL set yes" in norm
    assert "ACT_ACV_BASE_URL set yes" in norm


def test_check_accepts_config_flag(tmp_path, capsys):
    cfg = tmp_path / "act.toml"
    cfg.write_text('log_level = "ERROR"\n')
    code = main(["check", "--program", CAPE_PROGRAM, "--schema", CAPE_SCHEMA, "--config", str(cfg)])
    assert code == 0
    assert "PASS" in capsys.readouterr().out


def test_resolve_config_path(tmp_path, monkeypatch):
    from act.run import _resolve_config_path

    # An explicit --config wins (other args are ignored by the pre-parser).
    assert _resolve_config_path(["--config", "/x/act.toml", "--program", "p"]) == "/x/act.toml"
    # ./act.toml is auto-discovered only when present.
    monkeypatch.chdir(tmp_path)
    assert _resolve_config_path(["--program", "p"]) is None
    (tmp_path / "act.toml").write_text("")
    assert _resolve_config_path(["--program", "p"]) == "act.toml"
