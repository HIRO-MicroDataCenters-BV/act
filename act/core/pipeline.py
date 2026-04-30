import ast
from dataclasses import dataclass
from typing import List

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle, Violation


@dataclass
class PipelineResult:
    passed: bool
    violations: List[Violation]
    program_path: str
    parameterized: bool  # True if program reads from env/argv


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
        oracle: CorrectnessOracle,
        fuzz_runner=None,
        property_runner=None,
        acv=None,
    ):
        self._mock_generator = mock_generator
        self._oracle = oracle
        self._fuzz_runner = fuzz_runner
        self._property_runner = property_runner
        self._acv = acv

    def run(self, program_path: str) -> PipelineResult:
        violations: List[Violation] = []
        parameterized = _is_parameterized(program_path)

        mock_outputs = self._mock_generator.run_with_mocks(program_path)

        if parameterized:
            if self._fuzz_runner:
                violations.extend(self._fuzz_runner.run(program_path))
            if self._property_runner:
                violations.extend(self._property_runner.run(program_path))

        for resource_name, outputs in mock_outputs.items():
            resource_type = self._mock_generator.get_resource_type(resource_name)
            if resource_type:
                violations.extend(self._oracle.check(resource_type, outputs))

        if self._acv:
            try:
                acv_result = self._acv.validate(program_path)
                for finding in acv_result.findings:
                    violations.append(Violation(
                        field=finding.tool,
                        message=finding.message,
                        severity=finding.severity.upper(),
                    ))
            except Exception:
                pass

        return PipelineResult(
            passed=len(violations) == 0,
            violations=violations,
            program_path=program_path,
            parameterized=parameterized,
        )
