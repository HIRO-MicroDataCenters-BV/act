from act.reproducibility import PlanCheck, PlanCheckResult

CAPE_PROGRAM_VALID = "tests/fixtures/cape/path_a_valid.py"
CAPE_PROGRAM_NONDET = "tests/fixtures/cape/path_a_nondeterministic.py"
CAPE_SCHEMA = "tests/fixtures/cape/schema.json"


def test_deterministic_program():
    result = PlanCheck().run(CAPE_PROGRAM_VALID, CAPE_SCHEMA)
    assert isinstance(result, PlanCheckResult)
    assert result.deterministic is True
    assert result.hash_1 == result.hash_2
    assert result.diff == []
    assert result.capture_duration_ms > 0


def test_nondeterministic_program():
    result = PlanCheck().run(CAPE_PROGRAM_NONDET, CAPE_SCHEMA)
    assert result.deterministic is False
    assert result.hash_1 != result.hash_2
    assert len(result.diff) > 0
    assert len(result.diff) <= 5


def test_schema_list_is_accepted():
    result = PlanCheck().run(CAPE_PROGRAM_VALID, [CAPE_SCHEMA])
    assert result.deterministic is True
