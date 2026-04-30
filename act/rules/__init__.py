"""Dynamic rule loader — scans this package directory for rule modules.

Any .py file in act/rules/ that defines a register(oracle) function
is discovered and loaded automatically. No provider names are hardcoded here.
"""

import importlib
import pkgutil
from pathlib import Path


def auto_load(oracle) -> None:
    """Discover and register all rules found in this package."""
    package_dir = Path(__file__).parent
    package_name = __name__

    for module_info in pkgutil.iter_modules([str(package_dir)]):
        module = importlib.import_module(f"{package_name}.{module_info.name}")
        register = getattr(module, "register", None)
        if callable(register):
            register(oracle)
