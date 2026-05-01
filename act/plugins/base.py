from typing import List

from abc import ABC, abstractmethod

from act.core.oracle import Violation


class TestGeneratorPlugin(ABC):
    @abstractmethod
    def run(self, program_path: str) -> List[Violation]:
        """Run the generator against the program and return violations."""


class OraclePlugin(ABC):
    @abstractmethod
    def check(self, resource_type: str, inputs: dict) -> List[Violation]:
        """Check a resource and return violations."""
