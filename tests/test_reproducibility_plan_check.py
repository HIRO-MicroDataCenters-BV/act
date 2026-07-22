import subprocess
from unittest.mock import MagicMock, patch

import pytest

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


def test_capture_passes_timeout_to_subprocess():
    with patch(
        "act.reproducibility.plan_check.subprocess.run",
        return_value=MagicMock(stdout=b"{}"),
    ) as run_mock:
        PlanCheck(capture_timeout_s=7).run(CAPE_PROGRAM_VALID, CAPE_SCHEMA)
    assert run_mock.call_args_list  # captured twice
    assert all(call.kwargs.get("timeout") == 7 for call in run_mock.call_args_list)


def test_capture_timeout_propagates():
    # A hanging program is bounded: the TimeoutExpired surfaces (caught as exit 2 upstream).
    with patch(
        "act.reproducibility.plan_check.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="capture", timeout=1),
    ):
        with pytest.raises(subprocess.TimeoutExpired):
            PlanCheck(capture_timeout_s=1).run(CAPE_PROGRAM_VALID, CAPE_SCHEMA)
