"""Atheris-based fuzz runner for Path B programs."""

from typing import List

import logging
import os

log = logging.getLogger(__name__)

from act.core._runner_utils import (
    _atheris_mutate,
    collect_resource_info,
    deduplicate,
)
from act.core.oracle import CorrectnessOracle
from act.core.violations import Violation
from act.plugins.base import TestGeneratorPlugin


class FuzzRunner(TestGeneratorPlugin):
    """Mutates resource inputs with atheris and checks the oracle for violations."""

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

        resource_info = collect_resource_info(self._mg, program_path)
        violations: List[Violation] = []
        seen: set = set()

        for token, _name, base_outputs in resource_info:
            class_name = token.split(":")[-1]
            schema_inputs = self._mg._type_map.get(class_name, {}).get("inputs", {})

            for _ in range(self._iterations):
                raw = os.urandom(max(64, 4 * len(schema_inputs)))
                fdp = atheris.FuzzedDataProvider(raw)
                mutated = _atheris_mutate(base_outputs, schema_inputs, fdp)
                viols = self._oracle.check(token, mutated)
                violations.extend(deduplicate(viols, seen))

        log.debug("fuzz_runner.done", extra={"violations": len(violations)})
        return violations
