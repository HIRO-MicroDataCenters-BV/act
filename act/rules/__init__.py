"""Dynamic rule loader: any .py in act/rules/ defining register(oracle) is
auto-discovered and loaded. No provider names hardcoded.
"""

import importlib
import pkgutil
from pathlib import Path


class _SourcedOracle:
    """Forwards to the oracle, labelling each rule with the module that registered it.

    Lets a report say which rule set raised a violation without every rules module having
    to name itself.
    """

    def __init__(self, oracle, source: str):
        self._oracle = oracle
        self._source = source

    def add_rule(self, rule_fn, resource_type=None, source=None) -> None:
        self._oracle.add_rule(rule_fn, resource_type=resource_type, source=source or self._source)

    def __getattr__(self, name):
        return getattr(self._oracle, name)


def auto_load(oracle) -> None:
    """Discover and register all rules found in this package."""
    package_dir = Path(__file__).parent
    package_name = __name__

    for module_info in pkgutil.iter_modules([str(package_dir)]):
        module = importlib.import_module(f"{package_name}.{module_info.name}")
        register = getattr(module, "register", None)
        if callable(register):
            register(_SourcedOracle(oracle, module_info.name))
