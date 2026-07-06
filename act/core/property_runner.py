"""Hypothesis-based property runner for Path B programs.

Cross-platform; schema-derived strategies drive inputs and verify oracle invariants.
"""

from typing import List

import logging

from hypothesis import HealthCheck, given, settings

from act.core._runner_utils import (
    build_strategy,
    collect_resource_info,
    deduplicate,
)
from act.core.oracle import CorrectnessOracle
from act.core.violations import Violation
from act.plugins.base import TestGeneratorPlugin

log = logging.getLogger(__name__)


class PropertyRunner(TestGeneratorPlugin):
    """Checks the oracle against hypothesis-driven mutations of resource inputs.

    One program execution per run(); mutations reuse the captured outputs dict.
    """

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
        resource_info = collect_resource_info(self._mg, program_path)
        violations: List[Violation] = []
        seen: set = set()

        for token, _name, base_outputs in resource_info:
            class_name = token.split(":")[-1]
            schema_inputs = self._mg._type_map.get(class_name, {}).get("inputs", {})
            strategy = build_strategy(base_outputs, schema_inputs)
            self._check_token(token, strategy, violations, seen)

        log.debug("property_runner.done", extra={"violations": len(violations)})
        return violations

    def _check_token(self, token, strategy, violations, seen) -> None:
        @given(inputs=strategy)
        @settings(
            max_examples=self._max_examples,
            deadline=None,
            suppress_health_check=[HealthCheck.too_slow],
        )
        def _check(inputs):
            violations.extend(deduplicate(self._oracle.check(token, inputs), seen))

            # Invariant: status must be str or dict if present. Record as a
            # Violation, not an assert; a raise would crash the pipeline.
            status = inputs.get("status")
            if status is not None and not isinstance(status, (str, dict)):
                violations.extend(
                    deduplicate(
                        [
                            Violation(
                                field="status",
                                message=f"status must be str or dict, got {type(status).__name__}",
                                severity="HIGH",
                            )
                        ],
                        seen,
                    )
                )

        _check()
