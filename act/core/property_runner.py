"""Hypothesis-based property runner for parameterized programs (cross-platform).

The property is "the program has no violations under any input". Hypothesis searches the
inputs; when one breaks the property it shrinks it to the smallest failing input, which is
reported with the finding. The search then repeats, looking for a violation not yet found,
until the property holds or the limit of distinct findings is reached. Runs are
derandomized, so a program always gets the same verdict.
"""

from typing import List, Optional

import logging

from hypothesis import HealthCheck, Verbosity, given, settings
from hypothesis import strategies as st
from hypothesis.errors import HypothesisException

from act.core._runner_utils import (
    ARGV_KEY,
    INTERESTING_VALUES,
    check_inputs,
    discover_env_vars,
    reads_argv,
    record_inputs,
)
from act.core.oracle import CorrectnessOracle
from act.core.violations import Violation
from act.plugins.base import TestGeneratorPlugin

log = logging.getLogger(__name__)

_MAX_TEXT = 32


class _NewViolations(Exception):
    """Raised inside the property when an input produces a violation not yet found."""

    def __init__(self, inputs: dict, violations: List[Violation]):
        super().__init__(f"{len(violations)} new violation(s)")
        self.inputs = inputs
        self.violations = violations


def build_input_strategy(env_vars: list[str], uses_argv: bool):
    """Each env var unset / empty / interesting / arbitrary text; arguments as a short list."""
    value = st.one_of(st.just(""), st.sampled_from(INTERESTING_VALUES), st.text(max_size=_MAX_TEXT))
    per_var = st.one_of(st.none(), value)
    fields = {name: per_var for name in env_vars}
    if uses_argv:
        fields[ARGV_KEY] = st.lists(value, max_size=3)
    return st.fixed_dictionaries(fields)


class PropertyRunner(TestGeneratorPlugin):
    """Searches a parameterized program's inputs with hypothesis and reports the smallest failing ones."""

    def __init__(
        self,
        mock_generator,
        oracle: CorrectnessOracle,
        max_examples: int = 50,
        max_findings: int = 8,
    ):
        self._mg = mock_generator
        self._oracle = oracle
        self._max_examples = max_examples
        self._max_findings = max_findings

    def run(self, program_path: str) -> List[Violation]:
        log.debug("property_runner.start", extra={"program": program_path, "max_examples": self._max_examples})
        env_vars = discover_env_vars(program_path)
        uses_argv = reads_argv(program_path)
        if not env_vars and not uses_argv:
            return []

        strategy = build_input_strategy(env_vars, uses_argv)
        found: List[Violation] = []
        known: set = set()
        for _ in range(self._max_findings):
            failure = self._search(program_path, strategy, known)
            if failure is None:
                break
            for v in failure.violations:
                v.found_by, v.inputs = "property testing", failure.inputs
                known.add(v.key())
                found.append(v)

        log.debug("property_runner.done", extra={"violations": len(found)})
        return found

    def _search(self, program_path: str, strategy, known: set) -> Optional[_NewViolations]:
        """One hypothesis search for an input with a violation outside `known`; None if none."""

        @given(inputs=strategy)
        @settings(
            max_examples=self._max_examples,
            deadline=None,
            derandomize=True,
            database=None,
            verbosity=Verbosity.quiet,
            report_multiple_bugs=False,
            suppress_health_check=list(HealthCheck),
        )
        def _no_new_violations(inputs):
            env = {k: v for k, v in inputs.items() if k != ARGV_KEY}
            argv = inputs.get(ARGV_KEY)
            new = [v for v in check_inputs(self._mg, self._oracle, program_path, env, argv) if v.key() not in known]
            if new:
                raise _NewViolations(record_inputs(env, argv), new)

        try:
            _no_new_violations()
        except _NewViolations as failure:
            return failure  # hypothesis re-raises from the shrunk, smallest failing input
        except HypothesisException as exc:
            log.warning("property_runner.search_stopped", extra={"reason": type(exc).__name__})
        return None
