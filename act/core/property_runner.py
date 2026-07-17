"""Hypothesis-based property runner for Path B programs (cross-platform)."""

from typing import List

import logging

from hypothesis import HealthCheck, given, settings

from act.core._runner_utils import (
    build_env_strategy,
    check_env,
    discover_env_vars,
    generate_env_combinations,
)
from act.core.oracle import CorrectnessOracle
from act.core.violations import Violation
from act.plugins.base import TestGeneratorPlugin

log = logging.getLogger(__name__)


class PropertyRunner(TestGeneratorPlugin):
    """Explores env inputs via hypothesis (over the boundary set) and checks the oracle."""

    def __init__(
        self,
        mock_generator,
        oracle: CorrectnessOracle,
        max_examples: int = 50,
    ):
        self._mg = mock_generator
        self._oracle = oracle
        self._max_examples = max_examples

    def run(self, program_path: str) -> List[Violation]:
        log.debug("property_runner.start", extra={"program": program_path, "max_examples": self._max_examples})
        env_vars = discover_env_vars(program_path)
        if not env_vars:
            return []

        seen: set = set()
        violations: List[Violation] = []
        # Boundary combinations guarantee edge coverage; hypothesis adds value diversity.
        for combo in generate_env_combinations(env_vars):
            violations.extend(check_env(self._mg, self._oracle, program_path, combo, seen))
        self._explore(program_path, build_env_strategy(env_vars), violations, seen)

        log.debug("property_runner.done", extra={"violations": len(violations)})
        return violations

    def _explore(self, program_path, strategy, violations, seen) -> None:
        @given(env=strategy)
        @settings(max_examples=self._max_examples, deadline=None, suppress_health_check=[HealthCheck.too_slow])
        def _check(env):
            violations.extend(check_env(self._mg, self._oracle, program_path, env, seen))

        _check()
