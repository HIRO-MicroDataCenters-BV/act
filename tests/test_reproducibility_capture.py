import hashlib
import json
import subprocess
import sys
import textwrap

CAPE_PROGRAM = "tests/fixtures/cape/path_a_valid.py"
CAPE_SCHEMA = "tests/fixtures/cape/schema.json"
MULTI_PROGRAM = "tests/fixtures/multi_provider/program.py"
RANDOM_SCHEMA = "tests/fixtures/random/schema.json"


def _capture(program: str, *schemas: str) -> bytes:
    result = subprocess.run(
        [sys.executable, "-m", "act.reproducibility.capture", "--program", program, "--schema", *schemas],
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_stdout_is_valid_canonical_json():
    out = _capture(CAPE_PROGRAM, CAPE_SCHEMA)
    parsed = json.loads(out)
    assert "my-instance" in parsed
    assert "my-workspace" in parsed
    # canonical form has sorted keys at every level
    assert out == json.dumps(parsed, sort_keys=True, default=str).encode()


def test_two_invocations_same_hash():
    h1 = hashlib.sha256(_capture(CAPE_PROGRAM, CAPE_SCHEMA)).hexdigest()
    h2 = hashlib.sha256(_capture(CAPE_PROGRAM, CAPE_SCHEMA)).hexdigest()
    assert h1 == h2


def test_multi_schema_arg():
    out = _capture(MULTI_PROGRAM, CAPE_SCHEMA, RANDOM_SCHEMA)
    parsed = json.loads(out)
    assert "my-instance" in parsed
    assert "db-password" in parsed


def test_nondeterminism_changes_hash(tmp_path):
    program = tmp_path / "nondet.py"
    program.write_text(textwrap.dedent("""
        import uuid
        from pulumi_cape.compute import Instance
        from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs

        Instance(
            f"inst-{uuid.uuid4()}",
            spec=InstanceSpecArgs(
                boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot-vol")),
                sku_ref=ReferenceArgs(resource="skus/standard"),
                zone="zone-1",
                security_group_ref=ReferenceArgs(resource="security-groups/default"),
            ),
            workspace="default",
        )
    """))
    h1 = hashlib.sha256(_capture(str(program), CAPE_SCHEMA)).hexdigest()
    h2 = hashlib.sha256(_capture(str(program), CAPE_SCHEMA)).hexdigest()
    assert h1 != h2
