from typing import Iterable, Optional

from collections import Counter
from dataclasses import dataclass

# Key under which a finding's inputs record the program's arguments (not a valid env var name).
ARGV_KEY = "sys.argv"


@dataclass
class Violation:
    field: str
    message: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    # Which rule set raised it: "schema" for inference, otherwise the name a rule was
    # registered under ("cape", "checkov"). Stamped by the oracle, so rules need not set
    # it. Empty when a violation is built outside the oracle.
    source: str = ""
    # The resource it belongs to; set by the pipeline and the input runners.
    resource: str = ""
    # Set when fuzzing or property testing found it: which of the two, and the inputs that
    # triggered it (env var -> value, None = unset; "sys.argv" -> the arguments).
    found_by: str = ""
    inputs: Optional[dict] = None

    def key(self) -> tuple:
        """Identity for de-duplication: the same finding on the same resource."""
        return (self.resource, self.field, self.message)


def count_by_source(violations: Iterable[Violation]) -> dict:
    """How many violations each rule set raised, for reports that name the engine."""
    return dict(sorted(Counter(v.source or "unknown" for v in violations).items()))


def describe_inputs(inputs: dict) -> str:
    """Render the inputs that triggered a finding: set values, then the unset names."""
    set_parts = [f'{name}="{value}"' for name, value in inputs.items() if name != ARGV_KEY and value is not None]
    unset = [name for name, value in inputs.items() if name != ARGV_KEY and value is None]
    text = " ".join(set_parts)
    if unset:
        text += f" ({', '.join(unset)} unset)" if text else f"{', '.join(unset)} unset"
    if ARGV_KEY in inputs:
        args = ", ".join(f'"{a}"' for a in inputs[ARGV_KEY])
        text += f"{' ' if text else ''}sys.argv[1:]=[{args}]"
    return text
