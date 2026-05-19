"""CLI wiring tests for the runtime check.

These exercise act.run.main with all heavy dependencies mocked so we can
verify exit-code escalation and structured logging without spinning up
nixos-compose or Pulumi.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from act.reproducibility import (
    RuntimeCheckFailure,
    RuntimeCheckResult,
    TargetSpec,
)
from act.run import main

CAPE_PROGRAM = "tests/fixtures/cape/path_a_valid.py"
CAPE_SCHEMA = "tests/fixtures/cape/schema.json"


def _spec() -> TargetSpec:
    return TargetSpec(arch="x86_64-linux", orchestrator="k8s")


def _argv(*extra: str) -> list[str]:
    return [
        "--program", CAPE_PROGRAM,
        "--schema", CAPE_SCHEMA,
        *extra,
    ]


def test_cli_does_not_invoke_runtime_check_without_flag():
    with patch("act.run.RuntimeCheck") as RuntimeCheckMock:
        exit_code = main(_argv("--log-level", "ERROR"))
    RuntimeCheckMock.assert_not_called()
    assert exit_code == 0


def test_cli_invokes_runtime_check_with_flag():
    fake_result = RuntimeCheckResult(passed=True, substrate="nixos-compose", spec=_spec())
    rc = MagicMock()
    rc.run.return_value = fake_result

    with patch("act.run.RuntimeCheck", return_value=rc) as RuntimeCheckMock:
        exit_code = main(_argv("--check-deployment-runtime", "--log-level", "ERROR"))

    RuntimeCheckMock.assert_called_once()
    rc.run.assert_called_once()
    assert exit_code == 0


def test_cli_failure_escalates_exit_code():
    fake_result = RuntimeCheckResult(
        passed=False,
        substrate="nixos-compose",
        spec=_spec(),
        failures=[RuntimeCheckFailure(stage="output_mismatch", detail="hashes differ")],
    )
    rc = MagicMock()
    rc.run.return_value = fake_result

    with patch("act.run.RuntimeCheck", return_value=rc):
        exit_code = main(_argv("--check-deployment-runtime", "--log-level", "ERROR"))

    assert exit_code == 1


def test_cli_substrate_unavailable_does_not_fail_pipeline():
    fake_result = RuntimeCheckResult(
        passed=False,
        substrate="nixos-compose",
        spec=_spec(),
        failures=[RuntimeCheckFailure(stage="substrate_unavailable", detail="nxc missing")],
    )
    rc = MagicMock()
    rc.run.return_value = fake_result

    with patch("act.run.RuntimeCheck", return_value=rc):
        exit_code = main(_argv("--check-deployment-runtime", "--log-level", "ERROR"))

    assert exit_code == 0


def test_cli_writes_runtime_check_to_artefact(tmp_path):
    fake_result = RuntimeCheckResult(
        passed=True,
        substrate="nixos-compose",
        spec=_spec(),
        hash_1="x",
        hash_2="x",
        capture_duration_ms=42,
    )
    rc = MagicMock()
    rc.run.return_value = fake_result

    with patch("act.run.RuntimeCheck", return_value=rc):
        exit_code = main(_argv(
            "--check-deployment-runtime",
            "--output", str(tmp_path),
            "--log-level", "ERROR",
        ))

    assert exit_code == 0
    artefacts = list(tmp_path.glob("act_run_*.json"))
    assert len(artefacts) == 1
    data = json.loads(artefacts[0].read_text())
    assert data["runtime_check"]["passed"] is True
    assert data["runtime_check"]["substrate"] == "nixos-compose"
    assert data["runtime_check"]["capture_duration_ms"] == 42
