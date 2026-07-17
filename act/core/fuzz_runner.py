"""Atheris-driven fuzz runner for Path B programs (fuzzes env inputs, not code paths)."""

from typing import List

import logging
import os

from act.core._runner_utils import (
    _ENV_BOUNDARY_VALUES,
    discover_env_vars,
    explore_env_inputs,
    generate_env_combinations,
)
from act.core.oracle import CorrectnessOracle
from act.core.violations import Violation
from act.plugins.base import TestGeneratorPlugin

log = logging.getLogger(__name__)


def _atheris_env_combo(var_names: list[str], fdp) -> dict:
    return {name: _ENV_BOUNDARY_VALUES[fdp.ConsumeIntInRange(0, len(_ENV_BOUNDARY_VALUES) - 1)] for name in var_names}


class FuzzRunner(TestGeneratorPlugin):
    """Re-runs a parameterised program under fuzzed env inputs and checks the oracle."""

    def __init__(
        self,
        mock_generator,
        oracle: CorrectnessOracle,
        iterations: int = 100,
    ):
        self._mg = mock_generator
        self._oracle = oracle
        self._iterations = iterations

    def run(self, program_path: str) -> List[Violation]:
        log.debug("fuzz_runner.start", extra={"program": program_path, "iterations": self._iterations})
        try:
            import atheris
        except ImportError:
            log.debug("fuzz_runner.skipped", extra={"reason": "atheris_unavailable"})
            return []

        env_vars = discover_env_vars(program_path)
        if not env_vars:
            log.debug("fuzz_runner.skipped", extra={"reason": "no_env_inputs"})
            return []

        combos = generate_env_combinations(env_vars)
        for _ in range(max(0, self._iterations - len(combos))):
            raw = os.urandom(max(16, 4 * len(env_vars)))
            combos.append(_atheris_env_combo(env_vars, atheris.FuzzedDataProvider(raw)))

        violations = explore_env_inputs(self._mg, self._oracle, program_path, combos)
        log.debug("fuzz_runner.done", extra={"violations": len(violations)})
        return violations
