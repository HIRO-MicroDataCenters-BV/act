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

            # Capture loop variables explicitly to avoid late-binding in closure
            _token = token
            _oracle = self._oracle
            _violations = violations
            _seen = seen

            def _make_check(tok, orc, vlist, vseen):
                @given(inputs=strategy)
                @settings(
                    max_examples=self._max_examples,
                    deadline=None,
                    suppress_health_check=[HealthCheck.too_slow],
                )
                def _check(inputs):
                    viols = orc.check(tok, inputs)
                    vlist.extend(deduplicate(viols, vseen))

                    # Invariant: status must be str or dict if present. Record as a
                    # Violation, not an assert; a raise would propagate and crash the pipeline.
                    status = inputs.get("status")
                    if status is not None and not isinstance(status, (str, dict)):
                        vlist.extend(
                            deduplicate(
                                [
                                    Violation(
                                        field="status",
                                        message=f"status must be str or dict, got {type(status).__name__}",
                                        severity="HIGH",
                                    )
                                ],
                                vseen,
                            )
                        )

                return _check

            _make_check(_token, _oracle, _violations, _seen)()

        log.debug("property_runner.done", extra={"violations": len(violations)})
        return violations
