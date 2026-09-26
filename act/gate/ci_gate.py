from typing import Optional, Sequence

import logging
import sys
import traceback

from act.acv.models import acv_result_to_violations
from act.core.pipeline import ACTPipeline, PipelineResult
from act.core.violations import describe_inputs
from act.schema_resolver import provider_sdk_hint

log = logging.getLogger(__name__)


class CIGate:
    def __init__(self, pipeline: ACTPipeline):
        self._pipeline = pipeline
        # The most recent PipelineResult, for callers that want to summarise the run.
        # None until evaluate() completes without raising.
        self.last_result: Optional[PipelineResult] = None

    def evaluate(self, program_path: str, print_report: bool = True) -> int:
        """Run the pipeline and return exit code: 0 = pass, 1 = violations, 2 = error.

        ``print_report=False`` leaves printing to the caller, which can then add the
        failures of the checks it runs afterwards (see :meth:`format_report`).
        """
        try:
            result = self._pipeline.run(program_path)
            self.last_result = result
            # Nothing captured -> nothing validated: error (2), not a violation (1).
            if result.resource_count == 0:
                exit_code = 2
            else:
                exit_code = 0 if result.passed else 1
            log.info(
                "ci_gate.result",
                extra={
                    "program": program_path,
                    "passed": result.passed,
                    "violations": len(result.violations),
                    "exit_code": exit_code,
                },
            )
            if print_report:
                print(self.format_report(result))
            return exit_code
        except Exception as e:
            print(f"[ERROR] Pipeline failed: {e}", file=sys.stderr)
            hint = provider_sdk_hint(e)
            if hint:
                print(hint, file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 2

    def format_report(self, result: PipelineResult, layer_failures: Sequence[str] = ()) -> str:
        """The report; ``layer_failures`` (one line per failed later check) also make it FAIL."""
        if result.passed and not layer_failures:
            lines = [f"PASS  {result.program_path}"]
        else:
            lines = [f"FAIL  {result.program_path}"]
            for v in result.violations:
                where = f" on {v.resource}" if v.resource else ""
                lines.append(f"  [{v.severity}] {v.field}{where}: {v.message}")
                if v.found_by and v.inputs:
                    lines.append(f"      found by {v.found_by} with {describe_inputs(v.inputs)}")
            lines.extend(f"  {line}" for line in layer_failures)
        lines.extend(self._acv_lines(result))
        # Appended last so it never shifts the PASS/FAIL/ACV lines other callers assert on.
        if result.resource_count == 0:
            lines.append("WARN  no resources captured - nothing was validated")
        return "\n".join(lines)

    @staticmethod
    def _acv_lines(result: PipelineResult) -> list:
        """Render the ACV findings block (advisory by default, blocking when it gates the verdict)."""
        acv = result.acv_result
        if acv is None or not acv.findings:
            return []
        label = "blocking" if result.acv_blocking else "advisory"
        lines = [f"ACV ({label}): {len(acv.findings)} finding(s)"]
        for v in acv_result_to_violations(acv):
            lines.append(f"  [{v.severity}] {v.field}: {v.message}")
        return lines
