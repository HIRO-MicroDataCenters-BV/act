import glob
import json
import os

from act.reproducibility import (
    DeploymentArchResult,
    ImageBootFailure,
    PlanCheck,
    ReproducibilityArtefact,
    write_artefact,
)
from act.reproducibility.runtime_check import RuntimeCheckResult
from act.reproducibility.substrates.base import TargetSpec

CAPE_PROGRAM = "tests/fixtures/cape/path_a_valid.py"
CAPE_SCHEMA = "tests/fixtures/cape/schema.json"


def _read_artefact(output_dir: str) -> dict:
    paths = sorted(glob.glob(os.path.join(output_dir, "act_run_*.json")))
    assert len(paths) == 1, f"expected one artefact, found {paths}"
    with open(paths[0]) as f:
        return json.load(f)


def test_write_returns_path_under_output_dir(tmp_path):
    plan = PlanCheck().run(CAPE_PROGRAM, CAPE_SCHEMA)
    artefact = ReproducibilityArtefact(program_path=CAPE_PROGRAM, schemas=[CAPE_SCHEMA], plan_check=plan)
    path = write_artefact(artefact, str(tmp_path))
    assert path.startswith(str(tmp_path))
    assert os.path.basename(path).startswith("act_run_")
    assert path.endswith(".json")


def test_artefact_round_trips_plan_fields(tmp_path):
    plan = PlanCheck().run(CAPE_PROGRAM, CAPE_SCHEMA)
    artefact = ReproducibilityArtefact(program_path=CAPE_PROGRAM, schemas=[CAPE_SCHEMA], plan_check=plan)
    write_artefact(artefact, str(tmp_path))

    parsed = _read_artefact(str(tmp_path))
    assert parsed["program_path"] == CAPE_PROGRAM
    assert parsed["schemas"] == [CAPE_SCHEMA]
    assert parsed["plan_check"]["deterministic"] is True
    assert parsed["plan_check"]["hash_1"] == plan.hash_1
    assert parsed["plan_check"]["hash_2"] == plan.hash_2
    assert parsed["plan_check"]["capture_duration_ms"] == plan.capture_duration_ms
    assert parsed["plan_check"]["diff"] == []
    assert parsed["deployment_arch"] is None
    assert "captured_at" in parsed and parsed["captured_at"]
    assert "pulumi_version" in parsed and parsed["pulumi_version"] != ""


def test_artefact_includes_deployment_arch_when_set(tmp_path):
    plan = PlanCheck().run(CAPE_PROGRAM, CAPE_SCHEMA)
    arch = DeploymentArchResult(
        passed=False,
        arch="riscv64",
        images_checked=["nginx:1.25"],
        failures=[ImageBootFailure(image="nginx:1.25", reason="no_arch_variant", detail="no manifest")],
        capture_duration_ms=412,
    )
    artefact = ReproducibilityArtefact(
        program_path=CAPE_PROGRAM, schemas=[CAPE_SCHEMA], plan_check=plan, deployment_arch=arch
    )
    write_artefact(artefact, str(tmp_path))

    parsed = _read_artefact(str(tmp_path))
    assert parsed["deployment_arch"]["passed"] is False
    assert parsed["deployment_arch"]["arch"] == "riscv64"
    assert parsed["deployment_arch"]["images_checked"] == ["nginx:1.25"]
    assert parsed["deployment_arch"]["failures"][0]["reason"] == "no_arch_variant"
    assert parsed["deployment_arch"]["capture_duration_ms"] == 412


def test_write_creates_missing_directory(tmp_path):
    nested = tmp_path / "deep" / "nested" / "act_runs"
    plan = PlanCheck().run(CAPE_PROGRAM, CAPE_SCHEMA)
    artefact = ReproducibilityArtefact(program_path=CAPE_PROGRAM, schemas=[CAPE_SCHEMA], plan_check=plan)
    path = write_artefact(artefact, str(nested))
    assert os.path.exists(path)


def test_artefact_round_trips_runtime_check(tmp_path):
    plan = PlanCheck().run(CAPE_PROGRAM, CAPE_SCHEMA)
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    runtime = RuntimeCheckResult(
        passed=True,
        substrate="docker:linux/amd64",
        spec=spec,
        hash_1="aaa",
        hash_2="aaa",
        capture_duration_ms=12345,
    )
    artefact = ReproducibilityArtefact(
        program_path=CAPE_PROGRAM,
        schemas=[CAPE_SCHEMA],
        plan_check=plan,
        runtime_check=runtime,
    )
    write_artefact(artefact, str(tmp_path))

    parsed = _read_artefact(str(tmp_path))
    assert parsed["runtime_check"]["passed"] is True
    assert parsed["runtime_check"]["substrate"] == "docker:linux/amd64"
    assert parsed["runtime_check"]["spec"]["arch"] == "x86_64-linux"
    assert parsed["runtime_check"]["hash_1"] == "aaa"
    assert parsed["runtime_check"]["capture_duration_ms"] == 12345


def test_artefact_runtime_check_absent_by_default(tmp_path):
    plan = PlanCheck().run(CAPE_PROGRAM, CAPE_SCHEMA)
    artefact = ReproducibilityArtefact(program_path=CAPE_PROGRAM, schemas=[CAPE_SCHEMA], plan_check=plan)
    write_artefact(artefact, str(tmp_path))
    parsed = _read_artefact(str(tmp_path))
    assert parsed["runtime_check"] is None
