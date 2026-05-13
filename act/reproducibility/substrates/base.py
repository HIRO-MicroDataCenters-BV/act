from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Literal, Optional

ProvisionedKind = Literal["kubeconfig", "ssh", "http"]


@dataclass
class TargetSpec:
    arch: str
    orchestrator: Optional[str]
    features: list[str] = field(default_factory=list)


@dataclass
class ProvisionedTarget:
    endpoint: str
    kind: ProvisionedKind
    teardown: Callable[[], None]


class Substrate(ABC):
    name: ClassVar[str]

    @abstractmethod
    def matches(self, spec: TargetSpec) -> bool: ...

    @abstractmethod
    def provision(self, spec: TargetSpec) -> ProvisionedTarget: ...

    @abstractmethod
    def is_available(self) -> bool: ...
