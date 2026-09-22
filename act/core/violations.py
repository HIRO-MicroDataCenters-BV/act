from typing import Iterable

from collections import Counter
from dataclasses import dataclass


@dataclass
class Violation:
    field: str
    message: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    # Which rule set raised it: "schema" for inference, otherwise the name a rule was
    # registered under ("cape", "checkov"). Stamped by the oracle, so rules need not set
    # it. Empty when a violation is built outside the oracle.
    source: str = ""


def count_by_source(violations: Iterable[Violation]) -> dict:
    """How many violations each rule set raised, for reports that name the engine."""
    return dict(sorted(Counter(v.source or "unknown" for v in violations).items()))
