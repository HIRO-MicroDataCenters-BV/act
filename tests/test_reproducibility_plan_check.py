from act.reproducibility import ArchTarget, PlanCheck, PlanCheckResult

CAPE_PROGRAM_VALID = "tests/fixtures/cape/path_a_valid.py"
CAPE_PROGRAM_NONDET = "tests/fixtures/cape/path_a_nondeterministic.py"
CAPE_SCHEMA = "tests/fixtures/cape/schema.json"


def test_deterministic_program():
    result = PlanCheck().run(CAPE_PROGRAM_VALID, CAPE_SCHEMA)
    assert isinstance(result, PlanCheckResult)
    assert result.deterministic is True
    assert result.hash_1 == result.hash_2
    assert result.diff == []


def test_nondeterministic_program():
    result = PlanCheck().run(CAPE_PROGRAM_NONDET, CAPE_SCHEMA)
    assert result.deterministic is False
    assert result.hash_1 != result.hash_2
    assert len(result.diff) > 0
    assert len(result.diff) <= 5


def test_target_none_does_not_use_docker(monkeypatch):
    # Guard: when target is None, no docker subprocess should ever be spawned.
    import subprocess

    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        assert "docker" not in cmd[0], f"Docker should not be invoked when target=None, got {cmd}"
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    result = PlanCheck(target=None).run(CAPE_PROGRAM_VALID, CAPE_SCHEMA)
    assert result.deterministic is True


def test_schema_list_is_accepted():
    result = PlanCheck().run(CAPE_PROGRAM_VALID, [CAPE_SCHEMA])
    assert result.deterministic is True


def test_archtarget_stored_but_unused_for_host_run():
    pc = PlanCheck(target=ArchTarget.RISCV)
    assert pc._target is ArchTarget.RISCV
