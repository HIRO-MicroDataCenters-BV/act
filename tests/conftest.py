import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent  # src/
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cape_schema_path():
    return str(FIXTURES / "cape" / "schema.json")


@pytest.fixture
def cape_fixtures():
    return FIXTURES / "cape"


@pytest.fixture
def kubernetes_schema_path():
    path = ROOT / "examples" / "kubernetes" / "schema.json"
    if not path.exists():
        pytest.skip("kubernetes schema.json not found — run: pulumi package get-schema kubernetes")
    return str(path)


@pytest.fixture
def kubernetes_fixtures():
    return FIXTURES / "kubernetes"


@pytest.fixture
def random_schema_path():
    return str(FIXTURES / "random" / "schema.json")


@pytest.fixture
def random_fixtures():
    return FIXTURES / "random"


@pytest.fixture
def path_b_fixture():
    return FIXTURES / "cape" / "path_b_parameterized.py"
