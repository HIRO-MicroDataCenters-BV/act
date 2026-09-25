"""Atheris-driven fuzz runner for parameterized programs (fuzzes inputs, not code paths).

Every boundary combination of the program's inputs runs first (unset / empty / a sample
value per env var, and no / empty / one argument). Atheris then turns seeded random bytes
into new values: interesting strings such as wildcards and open CIDRs, and arbitrary text.
The input generation is random, not coverage-guided, and seeded so a program always gets
the same verdict.
"""

from typing import List, Optional

import itertools
import logging
import random

from act.core._runner_utils import (
    INTERESTING_VALUES,
    check_inputs,
    discover_env_vars,
    generate_env_combinations,
    reads_argv,
    record_inputs,
)
from act.core.oracle import CorrectnessOracle
from act.core.violations import Violation
from act.plugins.base import TestGeneratorPlugin

log = logging.getLogger(__name__)

_ARGV_BOUNDARIES: tuple = ((), ("",), ("act-fuzz",))
_MAX_TEXT = 32


def _fuzz_text(fdp, allow_unset: bool) -> Optional[str]:
    """One generated value: unset, empty, an interesting string, or arbitrary text."""
    choice = fdp.ConsumeIntInRange(0 if allow_unset else 1, 3)
    if choice == 0:
        return None
    if choice == 1:
        return ""
    if choice == 2:
        return INTERESTING_VALUES[fdp.ConsumeIntInRange(0, len(INTERESTING_VALUES) - 1)]
    return fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(1, _MAX_TEXT))


def _input_key(env: dict, argv: Optional[list]) -> tuple:
    return tuple(sorted(env.items(), key=str)), tuple(argv) if argv is not None else None


def generate_fuzz_inputs(
    env_vars: list[str], reads_argv: bool, iterations: int, seed: int = 0
) -> list[tuple[dict, Optional[list]]]:
    """Up to `iterations` distinct (env, argv) inputs: boundary combinations, then generated ones."""
    import atheris

    argv_options = [list(a) for a in _ARGV_BOUNDARIES] if reads_argv else [None]
    boundary = itertools.product(generate_env_combinations(env_vars) or [{}], argv_options)

    inputs: list[tuple[dict, Optional[list]]] = []
    tried: set = set()

    def add(env: dict, argv: Optional[list]) -> None:
        key = _input_key(env, argv)
        if key not in tried and len(inputs) < iterations:
            tried.add(key)
            inputs.append((env, argv))

    for env, argv in boundary:
        add(dict(env), argv)
    rng = random.Random(seed)
    for _ in range(iterations * 20):  # bounded: a tiny input space runs out of new values
        if len(inputs) >= iterations:
            break
        fdp = atheris.FuzzedDataProvider(rng.randbytes(256))
        env = {name: _fuzz_text(fdp, allow_unset=True) for name in env_vars}
        argv = [_fuzz_text(fdp, allow_unset=False) for _ in range(fdp.ConsumeIntInRange(0, 3))] if reads_argv else None
        add(env, argv)
    return inputs


class FuzzRunner(TestGeneratorPlugin):
    """Re-runs a parameterized program under fuzzed inputs and checks the oracle."""

    def __init__(
        self,
        mock_generator,
        oracle: CorrectnessOracle,
        iterations: int = 100,
        seed: int = 0,
    ):
        self._mg = mock_generator
        self._oracle = oracle
        self._iterations = iterations
        self._seed = seed

    def run(self, program_path: str) -> List[Violation]:
        log.debug("fuzz_runner.start", extra={"program": program_path, "iterations": self._iterations})
        try:
            import atheris  # noqa: F401  (only checking that it is installed)
        except ImportError:
            log.warning("fuzz_runner.skipped", extra={"reason": "atheris_unavailable"})
            return []

        env_vars = discover_env_vars(program_path)
        uses_argv = reads_argv(program_path)
        if not env_vars and not uses_argv:
            log.debug("fuzz_runner.skipped", extra={"reason": "no_inputs"})
            return []

        inputs = generate_fuzz_inputs(env_vars, uses_argv, self._iterations, self._seed)
        found: List[Violation] = []
        seen: set = set()
        for env, argv in inputs:
            for v in check_inputs(self._mg, self._oracle, program_path, env, argv):
                if v.key() not in seen:
                    seen.add(v.key())
                    v.found_by, v.inputs = "fuzzing", record_inputs(env, argv)
                    found.append(v)
        log.debug("fuzz_runner.done", extra={"inputs_tried": len(inputs), "violations": len(found)})
        return found
