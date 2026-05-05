from dataclasses import dataclass


@dataclass
class Violation:
    field: str
    message: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
