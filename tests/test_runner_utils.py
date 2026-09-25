from act.core._runner_utils import discover_env_vars, generate_env_combinations


def test_discover_env_vars_finds_fixture_vars(path_b_fixture):
    assert set(discover_env_vars(str(path_b_fixture))) == {
        "CAPE_ZONE",
        "CAPE_SSH_KEYS",
        "CAPE_SECURITY_GROUP_REF",
    }


def test_discover_env_vars_recognizes_all_read_forms(tmp_path):
    prog = tmp_path / "prog.py"
    prog.write_text("import os\na = os.getenv('A')\nb = os.environ['B']\nc = os.environ.get('C')\n")
    assert set(discover_env_vars(str(prog))) == {"A", "B", "C"}


def test_generate_env_combinations_full_cartesian_when_small():
    combos = generate_env_combinations(["X", "Y", "Z"])
    assert len(combos) == 27  # 3 boundary values ** 3 vars
    assert {"X": None, "Y": "act-fuzz", "Z": None} in combos


def test_generate_env_combinations_one_at_a_time_when_large():
    combos = generate_env_combinations(["A", "B", "C", "D", "E"])  # 3**5 = 243 > cap
    assert len(combos) == 1 + 5 * 2  # baseline + two non-default values per var
    assert {k: None for k in "ABCDE"} in combos


# ---------------------------------------------------------------------------
# Inputs a parameterized program is re-run under
# ---------------------------------------------------------------------------

from act.core._runner_utils import check_inputs, describe_inputs, reads_argv  # noqa: E402
from act.core.mock_generator import MockGenerator  # noqa: E402
from act.core.oracle import CorrectnessOracle  # noqa: E402
from act.core.violations import Violation  # noqa: E402
from act.rules import cape  # noqa: E402

ARGV_PROGRAM = """
import sys
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs

spec = {
    "boot_volume": VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot")),
    "sku_ref": ReferenceArgs(resource="skus/standard"),
    "zone": "zone-1",
}
if len(sys.argv) < 2 or sys.argv[1] != "public":
    spec["security_group_ref"] = ReferenceArgs(resource="security-groups/web")
Instance("vm-" + (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "default"), spec=InstanceSpecArgs(**spec))
"""


def test_violation_key_includes_the_resource():
    a = Violation("spec.sshKeys", "m", "HIGH", resource="vm-1")
    b = Violation("spec.sshKeys", "m", "HIGH", resource="vm-2")
    assert a.key() != b.key()
    assert a.key() == Violation("spec.sshKeys", "m", "LOW", resource="vm-1").key()


def test_describe_inputs_lists_set_values_then_unset_names():
    text = describe_inputs({"CAPE_ZONE": None, "CAPE_SECURITY_GROUP_REF": "", "CAPE_SSH_KEYS": None})
    assert text == 'CAPE_SECURITY_GROUP_REF="" (CAPE_ZONE, CAPE_SSH_KEYS unset)'


def test_describe_inputs_shows_arguments():
    assert describe_inputs({"A": "x", "sys.argv": ["public"]}) == 'A="x" sys.argv[1:]=["public"]'


def test_reads_argv_detects_sys_argv(tmp_path, path_b_fixture):
    prog = tmp_path / "prog.py"
    prog.write_text(ARGV_PROGRAM)
    assert reads_argv(str(prog)) is True
    assert reads_argv(str(path_b_fixture)) is False


def test_run_with_mocks_applies_argv(tmp_path, cape_schema_path):
    prog = tmp_path / "prog.py"
    prog.write_text(ARGV_PROGRAM)
    mg = MockGenerator(cape_schema_path)
    assert set(mg.run_with_mocks(str(prog), argv=["public"])) == {"vm-public"}
    assert set(mg.run_with_mocks(str(prog), argv=[])) == {"vm-default"}


def test_check_inputs_names_the_resource_of_each_violation(tmp_path, cape_schema_path):
    prog = tmp_path / "prog.py"
    prog.write_text(ARGV_PROGRAM)
    oracle = CorrectnessOracle(cape_schema_path)
    cape.register(oracle)
    found = check_inputs(MockGenerator(cape_schema_path), oracle, str(prog), env={}, argv=["public"])
    assert [(v.resource, v.field) for v in found] == [("vm-public", "spec.securityGroupRef")]
    assert check_inputs(MockGenerator(cape_schema_path), oracle, str(prog), env={}, argv=[]) == []
