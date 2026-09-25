import time

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.core.property_runner import PropertyRunner
from act.rules import cape

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

HARMLESS_PROGRAM = """
import os
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs

Instance("vm", spec=InstanceSpecArgs(
    boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot")),
    sku_ref=ReferenceArgs(resource="skus/standard"),
    zone=os.environ.get("CAPE_ZONE") or "zone-1",
    security_group_ref=ReferenceArgs(resource="security-groups/web"),
))
"""


def _runner(schema_path, max_examples=50):
    oracle = CorrectnessOracle(schema_path)
    cape.register(oracle)
    return PropertyRunner(MockGenerator(schema_path), oracle, max_examples=max_examples)


def _by_message(found):
    return {v.message: v for v in found}


def test_property_reports_the_smallest_failing_input(cape_schema_path, cape_fixtures):
    """The defaults are safe; the smallest change that exposes SSH is an empty security group."""
    found = _runner(cape_schema_path).run(str(cape_fixtures / "parameterized_secure_defaults.py"))
    ssh = [v for v in found if v.field == "spec.sshKeys"]
    assert ssh, [v.field for v in found]
    assert ssh[0].resource == "web-vm"
    assert ssh[0].found_by == "property testing"
    assert ssh[0].inputs == {"CAPE_ZONE": None, "CAPE_SSH_KEYS": None, "CAPE_SECURITY_GROUP_REF": ""}


def test_property_finds_each_distinct_violation_with_its_own_input(cape_schema_path, cape_fixtures):
    found = _by_message(_runner(cape_schema_path).run(str(cape_fixtures / "parameterized_kubernetes_api.py")))
    unrestricted = next(v for m, v in found.items() if "set restrictKubernetesApi" in m)
    world = next(v for m, v in found.items() if "0.0.0.0/0" in m)
    assert unrestricted.inputs == {"CAPE_API_ALLOWED_CIDR": ""}
    assert world.inputs == {"CAPE_API_ALLOWED_CIDR": "0.0.0.0/0"}


def test_property_result_is_the_same_on_every_run(cape_schema_path, cape_fixtures):
    program = str(cape_fixtures / "parameterized_secure_defaults.py")
    first = [(v.key(), v.inputs) for v in _runner(cape_schema_path).run(program)]
    assert first == [(v.key(), v.inputs) for v in _runner(cape_schema_path).run(program)]


def test_property_passes_program_whose_inputs_are_harmless(cape_schema_path, tmp_path):
    prog = tmp_path / "prog.py"
    prog.write_text(HARMLESS_PROGRAM)
    assert _runner(cape_schema_path).run(str(prog)) == []


def test_property_varies_program_arguments(cape_schema_path, tmp_path):
    prog = tmp_path / "prog.py"
    prog.write_text(ARGV_PROGRAM)
    found = _runner(cape_schema_path).run(str(prog))
    assert [v.field for v in found] == ["spec.securityGroupRef"]
    assert len(found[0].inputs["sys.argv"]) == 1 and found[0].inputs["sys.argv"][0]


def test_property_reports_each_finding_once(cape_schema_path, path_b_fixture):
    found = _runner(cape_schema_path).run(str(path_b_fixture))
    keys = [v.key() for v in found]
    assert keys and len(keys) == len(set(keys))


def test_property_runner_skips_program_without_inputs(cape_schema_path, cape_fixtures):
    assert _runner(cape_schema_path).run(str(cape_fixtures / "path_a_valid.py")) == []


def test_property_runner_stays_within_time(cape_schema_path, cape_fixtures):
    start = time.monotonic()
    _runner(cape_schema_path).run(str(cape_fixtures / "parameterized_secure_defaults.py"))
    elapsed = time.monotonic() - start
    assert elapsed < 30, f"PropertyRunner took {elapsed:.1f}s"
