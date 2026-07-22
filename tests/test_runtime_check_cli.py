"""CLI wiring tests for the runtime check.

These exercise act.run.main with all heavy dependencies mocked so we can
verify exit-code escalation and structured logging without spinning up
a real substrate or Pulumi.
"""

import json
from unittest.mock import MagicMock, patch

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
        "--program",
        CAPE_PROGRAM,
        "--schema",
        CAPE_SCHEMA,
        *extra,
    ]


def test_default_substrates_scales_slow_arch_timeouts():
    """riscv64 (emulated under QEMU) gets scaled provision timeouts; native amd64 stays at base."""
    from act.config import ActConfig
    from act.run import _SLOW_ARCH_TIMEOUT_SCALE, _default_substrates

    cfg = ActConfig(runtime_archs=("amd64", "arm64", "riscv64"))
    subs = _default_substrates(cfg)
    riscv = next(s for s in subs if s.platform == "linux/riscv64")
    amd64_base = next(s for s in subs if s.platform == "linux/amd64")

    assert riscv.startup_timeout == cfg.k3s_startup_timeout_s * _SLOW_ARCH_TIMEOUT_SCALE
    assert riscv.api_ready_timeout == cfg.k8s_api_ready_timeout_s * _SLOW_ARCH_TIMEOUT_SCALE
    assert amd64_base.startup_timeout == cfg.k3s_startup_timeout_s
    assert amd64_base.api_ready_timeout == cfg.k8s_api_ready_timeout_s


def test_reap_threshold_exceeds_slow_provision_budget(monkeypatch):
    """The reaper's age cutoff must exceed twice a riscv64 provision budget so a concurrent
    slow-booting run is never stopped."""
    import logging

    from act import run as run_mod
    from act.config import ActConfig

    captured = {}
    monkeypatch.setattr(run_mod, "reap_orphan_containers", lambda max_age_s: captured.update(max_age_s=max_age_s))
    fake_check = MagicMock()
    fake_check.run.return_value = RuntimeCheckResult(passed=True, substrate="x", spec=_spec())
    monkeypatch.setattr(run_mod, "RuntimeCheck", lambda **k: fake_check)

    cfg = ActConfig(runtime_archs=("amd64", "arm64", "riscv64"))
    run_mod._run_runtime_check("p.py", ["s.json"], logging.getLogger("t"), cfg)

    # riscv64 budget = (180+60)*4 = 960s; margin must be at least twice that.
    assert captured["max_age_s"] >= 2 * 960


def test_cli_does_not_invoke_runtime_check_without_flag():
    with patch("act.run.RuntimeCheck") as RuntimeCheckMock:
        exit_code = main(_argv("--log-level", "ERROR"))
    RuntimeCheckMock.assert_not_called()
    assert exit_code == 0


def test_cli_invokes_runtime_check_with_flag():
    fake_result = RuntimeCheckResult(passed=True, substrate="docker:linux/amd64", spec=_spec())
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
        substrate="docker:linux/amd64",
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
        substrate="docker:linux/amd64",
        spec=_spec(),
        failures=[RuntimeCheckFailure(stage="substrate_unavailable", detail="docker not available")],
    )
    rc = MagicMock()
    rc.run.return_value = fake_result

    with patch("act.run.RuntimeCheck", return_value=rc):
        exit_code = main(_argv("--check-deployment-runtime", "--log-level", "ERROR"))

    assert exit_code == 0


def test_cli_writes_runtime_check_to_artefact(tmp_path):
    fake_result = RuntimeCheckResult(
        passed=True,
        substrate="docker:linux/amd64",
        spec=_spec(),
        hash_1="x",
        hash_2="x",
        capture_duration_ms=42,
    )
    rc = MagicMock()
    rc.run.return_value = fake_result

    with patch("act.run.RuntimeCheck", return_value=rc):
        exit_code = main(
            _argv(
                "--check-deployment-runtime",
                "--output",
                str(tmp_path),
                "--log-level",
                "ERROR",
            )
        )

    assert exit_code == 0
    artefacts = list(tmp_path.glob("act_run_*.json"))
    assert len(artefacts) == 1
    data = json.loads(artefacts[0].read_text())
    assert data["runtime_check"]["passed"] is True
    assert data["runtime_check"]["substrate"] == "docker:linux/amd64"
    assert data["runtime_check"]["capture_duration_ms"] == 42
