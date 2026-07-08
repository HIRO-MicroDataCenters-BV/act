import logging
import sys
import traceback

from act.acv.models import acv_result_to_violations
from act.core.pipeline import ACTPipeline, PipelineResult

log = logging.getLogger(__name__)


class CIGate:
    def __init__(self, pipeline: ACTPipeline):
        self._pipeline = pipeline

    def evaluate(self, program_path: str) -> int:
        """Run the pipeline and return exit code: 0 = pass, 1 = violations, 2 = error."""
        try:
            result = self._pipeline.run(program_path)
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
            print(self.format_report(result))
            return exit_code
        except Exception as e:
            print(f"[ERROR] Pipeline failed: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 2

    def format_report(self, result: PipelineResult) -> str:
        if result.passed:
            lines = [f"PASS  {result.program_path}"]
        else:
            lines = [f"FAIL  {result.program_path}"]
            for v in result.violations:
                lines.append(f"  [{v.severity}] {v.field}: {v.message}")
        lines.extend(self._acv_lines(result))
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
