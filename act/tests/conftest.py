import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent  # src/
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cape_schema_path():
    return str(FIXTURES / "cape" / "schema.json")


@pytest.fixture
def generic_schema_path():
    return str(FIXTURES / "generic" / "schema.json")


@pytest.fixture
def cape_fixtures():
    return FIXTURES / "cape"


@pytest.fixture
def generic_fixtures():
    return FIXTURES / "generic"
