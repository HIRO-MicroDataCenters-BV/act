"""Image-build pipeline validation: nxc + nix consume our rendered k8s flake.

Builds a tiny container with nix + nxc pre-installed
(`tests/integration/nixos_compose/Dockerfile`) and uses it to verify that
`image_helpers.nxc_compose.render_k8s_composition` produces a Nix flake that
exposes `packages.x86_64-linux.default` — the input shape `nxc build` expects.

These checks belong with the image-build pipeline (the helpers are used by CI
to produce the runtime substrate images consumed by DockerSubstrate).

Skipped automatically when docker isn't available so unit-test suites stay
green on contributor machines without docker.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from act.reproducibility.image_helpers.nxc_compose import render_k8s_composition

DOCKERFILE_DIR = Path(__file__).parent / "integration" / "nixos_compose"
IMAGE_TAG = "act-nxc:integration"
IMAGE_TAG_AMD64 = "act-nxc:amd64"


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


def _ensure_amd64_image() -> None:
    """Build the explicit amd64 variant so nxc evaluates and builds natively
    for the x86_64-linux target our substrate emits. On an aarch64 host this
    runs under QEMU emulation — slow but completes."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE_TAG_AMD64],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", IMAGE_TAG_AMD64, str(DOCKERFILE_DIR)],
        check=True,
        timeout=1800,
    )


def test_rendered_composition_is_a_valid_flake(tmp_path):
    _ensure_image()

    composition = render_k8s_composition(arch="x86_64-linux", flavour="docker")
    (tmp_path / "flake.nix").write_text(composition)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/work",
            "-w",
            "/work",
            IMAGE_TAG,
            "nix",
            "flake",
            "show",
            "path:/work",
            "--no-write-lock-file",
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

    Smoke-level check on the CLI shape: nxc accepts both subcommands and
    progresses *into* the derivation evaluation phase. Errors like
    'unrecognized arg' or 'composition environment missing' would mean the
    substrate's CLI invocation order or flags are wrong.

    Uses x86_64-linux; on an aarch64 host the cross-compile build will fail
    after evaluation, which is fine for this check — we're verifying nxc
    accepts our flake, not that the build completes.
    """
    _ensure_image()

    composition = render_k8s_composition(arch="x86_64-linux", flavour="docker")
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
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/work",
            "-w",
            "/work",
            IMAGE_TAG,
            "bash",
            "-c",
            script,
        ],
        capture_output=True,
        timeout=300,
    )

    out = result.stdout.decode() + result.stderr.decode()
    assert "INIT_FAILED" not in out, f"nxc init rejected the composition:\n{out}"
    assert "No such option" not in out, f"nxc rejected a flag we passed:\n{out}"
    assert "Missing nixos composition environment" not in out, f"nxc build ran without nxc init first:\n{out}"


def test_full_nxc_build_completes_for_x86_64_under_emulation(tmp_path):
    """End-to-end: nxc init + nxc build run to completion against the substrate's
    x86_64-linux composition. Proves the substrate produces a build-able flake
    that nxc actually consumes and produces a docker-compose artefact.

    On Apple Silicon the amd64 image runs under QEMU emulation; expect ~5 min.
    On an x86_64 host this is native (~2 min). Image building (one-off): ~5 min.

    Requires the host docker socket because nxc's docker flavour invokes
    `docker save` inside the final derivation to materialise the image layer.
    """
    _ensure_amd64_image()

    composition = render_k8s_composition(arch="x86_64-linux", flavour="docker")
    (tmp_path / "flake.nix").write_text(composition)

    script = (
        "set -uxo pipefail\n"
        "cd /work\n"
        "rm -rf nxc build nxc.json flake.lock\n"
        "nxc init -f docker\n"
        "nxc build -f docker /work/flake.nix\n"
        "build_exit=$?\n"
        "echo BUILD_EXIT=$build_exit\n"
        "ls -la nxc/build/ 2>&1\n"
        "exit $build_exit\n"
    )

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "-v",
            f"{tmp_path}:/work",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-w",
            "/work",
            IMAGE_TAG_AMD64,
            "bash",
            "-c",
            script,
        ],
        capture_output=True,
        timeout=1800,
    )

    out = result.stdout.decode() + result.stderr.decode()
    assert "BUILD_EXIT=0" in out, (
        "nxc build did not complete:\n"
        f"--- stdout (tail) ---\n{result.stdout.decode()[-2000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr.decode()[-2000:]}"
    )
    # The build symlink convention is `nxc/build/<composition-name>::<flavour>`.
    assert "::docker" in out, f"nxc build did not produce a docker-flavoured symlink:\n{out[-1500:]}"
    # The build must invoke docker to materialise the image layer.
    assert (
        "Docker Image loaded" in out or "Build completed" in out
    ), f"nxc build did not reach docker-image-load step:\n{out[-1500:]}"
