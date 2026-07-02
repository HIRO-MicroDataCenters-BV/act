from typing import List, Optional, Protocol

import ast
import logging
import time
from dataclasses import dataclass

from act.acv.models import ACVResult
from act.core.mock_generator import MockGenerator
from act.core.violations import Violation
from act.plugins.base import OraclePlugin

log = logging.getLogger(__name__)


def _ms(t: float) -> int:
    return int((time.perf_counter() - t) * 1000)


class _Validator(Protocol):
    def validate(self, program_path: str) -> ACVResult: ...


@dataclass
class PipelineResult:
    passed: bool
    violations: List[Violation]
    program_path: str
    parameterized: bool  # True if program reads from env/argv
    acv_result: Optional[ACVResult] = None  # advisory only — never affects `passed`


def _is_parameterized(program_path: str) -> bool:
    """Return True if the program reads from os.environ or sys.argv."""
    with open(MockGenerator._entry_point(program_path)) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr == "environ":
                return True
            if node.value.id == "sys" and node.attr == "argv":
                return True
    return False


class ACTPipeline:
    def __init__(
        self,
        mock_generator: MockGenerator,
        oracle: OraclePlugin,
        fuzz_runner=None,
        property_runner=None,
        acv: Optional[_Validator] = None,
    ):
        self._mock_generator = mock_generator
        self._oracle = oracle
        self._fuzz_runner = fuzz_runner
        self._property_runner = property_runner
        self._acv = acv

    def run(self, program_path: str) -> PipelineResult:
        t0 = time.perf_counter()
        violations: List[Violation] = []
        parameterized = _is_parameterized(program_path)
        log.info("pipeline.start", extra={"program": program_path, "parameterized": parameterized})

        t = time.perf_counter()
        mock_outputs = self._mock_generator.run_with_mocks(program_path)
        log.info("pipeline.mock_done", extra={"resources": list(mock_outputs), "duration_ms": _ms(t)})

        if parameterized:
            if self._fuzz_runner:
                t = time.perf_counter()
                fuzz_v = self._fuzz_runner.run(program_path)
                violations.extend(fuzz_v)
                log.info("pipeline.fuzz_done", extra={"violations": len(fuzz_v), "duration_ms": _ms(t)})
            if self._property_runner:
                t = time.perf_counter()
                prop_v = self._property_runner.run(program_path)
                violations.extend(prop_v)
                log.info("pipeline.property_done", extra={"violations": len(prop_v), "duration_ms": _ms(t)})
        t = time.perf_counter()
        oracle_violations: List[Violation] = []
        for resource_name, outputs in mock_outputs.items():
            resource_type = self._mock_generator.get_resource_type(resource_name)
            if resource_type:
                oracle_violations.extend(self._oracle.check(resource_type, outputs))
        violations.extend(oracle_violations)
        log.info("pipeline.oracle_done", extra={"violations": len(oracle_violations), "duration_ms": _ms(t)})

        # ACV runs after the deterministic oracle and is additive: its findings
        # are surfaced in the report but never added to `violations`, so they do
        # not affect `passed`/exit code. Unavailability skips gracefully.
        acv_result: Optional[ACVResult] = None
        if self._acv:
            t = time.perf_counter()
            acv_result = self._acv.validate(program_path)
            log.info(
                "pipeline.acv_done",
                extra={
                    "verdict": acv_result.verdict,
                    "risk_level": acv_result.risk_level,
                    "iterations": acv_result.iterations,
                    "duration_ms": _ms(t),
                },
            )

        log.info(
            "pipeline.done",
            extra={
                "passed": len(violations) == 0,
                "violations": len(violations),
                "duration_ms": _ms(t0),
            },
        )

        return PipelineResult(
            passed=len(violations) == 0,
            violations=violations,
            program_path=program_path,
            parameterized=parameterized,
            acv_result=acv_result,
        )
