"""Provider-SDK diagnostics: the hint on a failed import, and `act doctor --program`.

ACT executes the program, so every provider SDK it imports must be importable in the
environment ACT runs in. These tests cover telling the user which one is missing.
"""

import pytest

from act.run import main

CAPE_PROGRAM = "tests/fixtures/cape/path_a_valid.py"
CAPE_SCHEMA = "tests/fixtures/cape/schema.json"


@pytest.fixture
def program_with_missing_sdk(tmp_path):
    """A program importing an uninstalled provider. Its schema is supplied too, so
    schema resolution succeeds and the failure under test is the import itself."""
    schema = tmp_path / "notinstalled.json"
    schema.write_text('{"name": "notinstalled", "resources": {}}')
    prog = tmp_path / "missing_sdk.py"
    prog.write_text("import pulumi_notinstalled\n")
    return str(prog), str(schema)


def test_missing_provider_sdk_prints_hint(capsys, program_with_missing_sdk):
    program, schema = program_with_missing_sdk
    assert main(["check", "--program", program, "--schema", schema]) == 2
    err = capsys.readouterr().err
    assert "[HINT]" in err
    assert "pulumi_notinstalled" in err
    assert "pulumi-notinstalled" in err


def test_unrelated_failure_prints_no_provider_hint(capsys, tmp_path):
    prog = tmp_path / "boom.py"
    prog.write_text("raise ValueError('unrelated')\n")
    assert main(["check", "--program", str(prog), "--schema", CAPE_SCHEMA]) == 2
    assert "[HINT]" not in capsys.readouterr().err


def test_doctor_reports_importable_provider_sdk(capsys):
    assert main(["doctor", "--program", CAPE_PROGRAM]) == 0
    out = capsys.readouterr().out
    assert "pulumi_cape" in out
    assert "ok" in out


def test_doctor_reports_missing_provider_sdk(capsys, program_with_missing_sdk):
    program, _ = program_with_missing_sdk
    assert main(["doctor", "--program", program]) == 0
    out = capsys.readouterr().out
    assert "pulumi_notinstalled" in out
    assert "missing" in out
    assert "pip install pulumi-notinstalled" in out


def test_doctor_without_program_omits_provider_section(capsys):
    assert main(["doctor"]) == 0
    assert "provider SDKs" not in capsys.readouterr().out


def test_doctor_on_program_without_providers(capsys, tmp_path):
    prog = tmp_path / "plain.py"
    prog.write_text("x = 1\n")
    assert main(["doctor", "--program", str(prog)]) == 0
    assert "no provider SDKs imported" in capsys.readouterr().out


def test_doctor_on_missing_program_file(capsys, tmp_path):
    assert main(["doctor", "--program", str(tmp_path / "absent.py")]) == 0
    assert "cannot read the program" in capsys.readouterr().out


def test_doctor_on_unparseable_program(capsys, tmp_path):
    prog = tmp_path / "broken.py"
    prog.write_text("def (:\n")
    assert main(["doctor", "--program", str(prog)]) == 0
    assert "cannot read the program" in capsys.readouterr().out
