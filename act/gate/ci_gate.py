import logging

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
            log.info("ci_gate.result", extra={
                "program": program_path,
                "passed": result.passed,
                "violations": len(result.violations),
                "exit_code": exit_code,
            })
            print(self.format_report(result))
            return exit_code
        except Exception as e:
            print(f"[ERROR] Pipeline failed: {e}")
            return 2

    def format_report(self, result: PipelineResult) -> str:
        if result.passed:
            return f"PASS  {result.program_path}"
        lines = [f"FAIL  {result.program_path}"]
        for v in result.violations:
            lines.append(f"  [{v.severity}] {v.field}: {v.message}")
        return "\n".join(lines)
