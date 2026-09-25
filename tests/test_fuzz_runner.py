import logging
import sys

import pytest

from act.core._runner_utils import _ENV_BOUNDARY_VALUES
from act.core.fuzz_runner import FuzzRunner, generate_fuzz_inputs
from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.rules import cape

# Fuzzing needs atheris, which installs on Linux; these run in a Linux container (see the
# progress doc). On a machine without it only the skip tests run.
needs_atheris = pytest.mark.skipif(
    __import__("importlib").util.find_spec("atheris") is None, reason="atheris not installed"
)

ARGV_PROGRAM = """
import sys
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs

spec = {
    "boot_volume": VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot")),
    "sku_ref": ReferenceArgs(resource="skus/standard"),
    "zone": "zone-1",
}
if not (len(sys.argv) > 1 and sys.argv[1]):
    spec["security_group_ref"] = ReferenceArgs(resource="security-groups/web")
Instance("vm", spec=InstanceSpecArgs(**spec))
"""


def _runner(schema_path, iterations=100):
    oracle = CorrectnessOracle(schema_path)
    cape.register(oracle)
    return FuzzRunner(MockGenerator(schema_path), oracle, iterations=iterations)


def test_fuzz_runner_skips_without_atheris(cape_schema_path, path_b_fixture, monkeypatch, caplog):
    """The skip is a WARNING: at the default log level a user must see that fuzzing did not run."""
    monkeypatch.setitem(sys.modules, "atheris", None)
    with caplog.at_level(logging.WARNING, logger="act.core.fuzz_runner"):
        assert _runner(cape_schema_path).run(str(path_b_fixture)) == []
    skipped = [r for r in caplog.records if r.getMessage() == "fuzz_runner.skipped"]
    assert skipped and skipped[0].levelno == logging.WARNING
    assert getattr(skipped[0], "reason", None) == "atheris_unavailable"


@needs_atheris
def test_fuzz_runner_skips_program_without_inputs(cape_schema_path, cape_fixtures):
    assert _runner(cape_schema_path).run(str(cape_fixtures / "path_a_valid.py")) == []


@needs_atheris
def test_fuzz_inputs_go_beyond_the_boundary_values():
    inputs = generate_fuzz_inputs(["A"], reads_argv=False, iterations=60, seed=0)
    values = {env["A"] for env, _ in inputs}
    assert set(_ENV_BOUNDARY_VALUES) <= values
    assert values - set(_ENV_BOUNDARY_VALUES), "fuzzing tried nothing but the fixed boundary values"


@needs_atheris
def test_fuzz_inputs_are_unique_and_reproducible():
    first = generate_fuzz_inputs(["A", "B"], reads_argv=True, iterations=80, seed=0)
    assert first == generate_fuzz_inputs(["A", "B"], reads_argv=True, iterations=80, seed=0)
    keys = [(tuple(sorted(env.items(), key=str)), tuple(argv or ())) for env, argv in first]
    assert len(keys) == len(set(keys))
    assert len(first) == 80


@needs_atheris
def test_fuzz_finds_value_that_opens_the_kubernetes_api(cape_schema_path, cape_fixtures):
    """0.0.0.0/0 is neither unset, empty, nor the sample value: only generated values reach it."""
    found = _runner(cape_schema_path).run(str(cape_fixtures / "parameterized_kubernetes_api.py"))
    world = [v for v in found if "0.0.0.0/0" in v.message]
    assert world, [v.message for v in found]
    assert world[0].resource == "api-cluster"
    assert world[0].found_by == "fuzzing"
    assert world[0].inputs == {"CAPE_API_ALLOWED_CIDR": "0.0.0.0/0"}


@needs_atheris
def test_fuzz_varies_program_arguments(cape_schema_path, tmp_path):
    prog = tmp_path / "prog.py"
    prog.write_text(ARGV_PROGRAM)
    found = _runner(cape_schema_path).run(str(prog))
    assert [v.field for v in found] == ["spec.securityGroupRef"]
    assert found[0].inputs["sys.argv"], "the finding should record the argument that triggered it"


@needs_atheris
def test_fuzz_reports_each_finding_once(cape_schema_path, path_b_fixture):
    found = _runner(cape_schema_path, iterations=200).run(str(path_b_fixture))
    keys = [v.key() for v in found]
    assert keys and len(keys) == len(set(keys))
    assert all(v.inputs is not None and v.found_by == "fuzzing" for v in found)
