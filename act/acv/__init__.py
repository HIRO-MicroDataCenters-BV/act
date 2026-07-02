"""ACT Cognitive Validator (ACV).

Only the dependency-free data models are re-exported here so importing this
package stays cheap on the core pipeline path. Import the agent explicitly via
``from act.acv.agent import ACTCognitiveValidator`` (it guards the optional
``acv`` extra internally).
"""

from act.acv.models import (
    ACVFinding,
    ACVResult,
    acv_result_to_violations,
    findings_from_tool_json,
)

__all__ = [
    "ACVFinding",
    "ACVResult",
    "acv_result_to_violations",
    "findings_from_tool_json",
]
