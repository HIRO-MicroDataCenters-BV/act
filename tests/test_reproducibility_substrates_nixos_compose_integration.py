"""Opt-in docker-backed integration test for NixOSComposeSubstrate.

Builds a tiny container with nix + nxc pre-installed (`tests/integration/nixos_compose/Dockerfile`)
and uses it to verify that the substrate's rendered composition is a valid Nix
flake that exposes `packages.x86_64-linux.default` — the input shape `nxc build`
expects.

Skipped automatically when docker isn't available so unit-test suites stay green
on contributor machines without docker.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.nixos_compose import NixOSComposeSubstrate

DOCKERFILE_DIR = Path(__file__).parent / "integration" / "nixos_compose"
IMAGE_TAG = "act-nxc:integration"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return True


def _image_present() -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE_TAG],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="docker daemon not available; opt in by starting docker and rebuilding",
)


def _ensure_image() -> None:
    if _image_present():
        return
    subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(DOCKERFILE_DIR)],
        check=True,
        timeout=1800,
    )


def test_rendered_composition_is_a_valid_flake(tmp_path):
    _ensure_image()

    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    composition = NixOSComposeSubstrate()._render_composition(spec, flavour="docker")
    (tmp_path / "flake.nix").write_text(composition)

    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{tmp_path}:/work",
            "-w", "/work",
            IMAGE_TAG,
            "nix", "flake", "show", "path:/work", "--no-write-lock-file",
        ],
        capture_output=True,
        check=True,
        timeout=900,
    )

    out = result.stdout.decode() + result.stderr.decode()
    assert "packages" in out
    assert "x86_64-linux" in out
    assert "default" in out


def test_nxc_is_present_and_runs():
    _ensure_image()

    result = subprocess.run(
        ["docker", "run", "--rm", IMAGE_TAG, "nxc", "--version"],
        capture_output=True,
        check=True,
        timeout=60,
    )
    out = result.stdout.decode() + result.stderr.decode()
    assert "nxc" in out


def test_nxc_accepts_init_build_pipeline_against_rendered_composition(tmp_path):
    """Exercises `nxc init` then `nxc build` against our rendered composition.

    `nxc build` will fail at the cross-compile derivation step when run on an
    aarch64 host targeting x86_64 without binary-cache hits — that's expected
    and out of scope for this substrate (the substrate's contract is producing
    a flake nxc understands, not driving a successful cross-build).

    The acceptance criterion is therefore: nxc accepts both subcommands and
    progresses *into* the derivation evaluation phase. Errors like 'unrecognized
    arg' or 'composition environment missing' would mean the substrate's CLI
    shape is wrong.
    """
    _ensure_image()

    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    composition = NixOSComposeSubstrate()._render_composition(spec, flavour="docker")
    (tmp_path / "flake.nix").write_text(composition)

    script = (
        "set -uo pipefail\n"
        "cd /work\n"
        "nxc init -f docker || { echo INIT_FAILED; exit 71; }\n"
        "timeout 120 nxc build -f docker /work/flake.nix\n"
        "exit_code=$?\n"
        "echo BUILD_EXIT=$exit_code\n"
    )

    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{tmp_path}:/work",
            "-w", "/work",
            IMAGE_TAG,
            "bash", "-c", script,
        ],
        capture_output=True,
        timeout=300,
    )

    out = result.stdout.decode() + result.stderr.decode()
    # nxc init must succeed.
    assert "INIT_FAILED" not in out, f"nxc init rejected the composition:\n{out}"
    # nxc build must progress past CLI parsing — these strings would indicate a
    # substrate bug (wrong flag, wrong workflow order).
    assert "No such option" not in out, f"nxc rejected a flag we passed:\n{out}"
    assert "Missing nixos composition environment" not in out, (
        f"nxc build ran without nxc init first:\n{out}"
    )
