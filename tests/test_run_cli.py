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


def test_check_missing_schema_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", "--program", CAPE_PROGRAM])
    assert exc.value.code == 2
    assert "required" in capsys.readouterr().err


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
