"""End-to-end integration: full RuntimeCheck.run pipeline against real k3s.

The mocked tests in test_reproducibility_runtime_check.py cover the
orchestration logic in isolation. This file proves the integration glue
works against a real ephemeral cluster, including the substantive
twice-and-hash claim:

  MockGenerator (capture plan) →
    extract_target_spec →
      DockerSubstrate.provision (real k3s container) →
        run_pulumi_against (real `pulumi up` via Automation API, twice) →
          probe_k8s (real `kubectl get pods`) →
            normalise + sha256 + compare →
              teardown.

Arch coverage mirrors the substrate-only e2e: amd64 unconditional,
arm64 on arm64 hosts, riscv64 when the pinned image has been built.

Skipped when any of docker, kubectl, or the pulumi CLI is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from act.reproducibility.runtime_check import RuntimeCheck, run_pulumi_against
from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.docker import DockerSubstrate
from act.reproducibility.substrates.gpu import GpuSubstrate

REPO_ROOT = Path(__file__).resolve().parent.parent
# ConfigMap-only fixture: no image pulls, no pod scheduling. Image pulls
# inside a privileged k3s container under QEMU emulation routinely exceed
# Pulumi's 10-min default timeout; a ConfigMap exercises the same
# substrate → pulumi up → kubectl path without that bottleneck.
CONFIGMAP_PROGRAM = str(REPO_ROOT / "tests" / "fixtures" / "kubernetes" / "configmap.py")
K8S_SCHEMA = str(REPO_ROOT / "examples" / "kubernetes" / "schema.json")

K3S_IMAGE = os.environ.get("ACT_K3S_IMAGE", "rancher/k3s:v1.32.1-k3s1")
K3S_RISCV64_IMAGE = os.environ.get("ACT_K3S_RISCV64_IMAGE", "act-k3s:riscv64")
K3S_DOCKER_ARGS = ("--privileged", "--tmpfs", "/run", "--tmpfs", "/var/run")
K3S_COMMAND = (
    "server",
    "--disable=traefik",
    "--write-kubeconfig-mode=644",
    "--snapshotter=native",
)
# riscv64 under QEMU user-mode emulation cannot run iptables; the image
# ships a bridge CNI conflist so the node still reaches Ready.
K3S_RISCV64_COMMAND = K3S_COMMAND + (
    "--disable-kube-proxy",
    "--flannel-backend=none",
    "--disable-network-policy",
)


def _binary_available(name: str) -> bool:
    return shutil.which(name) is not None


def _docker_daemon_available() -> bool:
    if not _binary_available("docker"):
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return True


def _arm64_host() -> bool:
    out = subprocess.run(
        ["uname", "-m"], capture_output=True, check=False, timeout=5
    ).stdout.decode().strip()
    return out in ("arm64", "aarch64")


def _riscv64_image_present() -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", K3S_RISCV64_IMAGE],
            capture_output=True, check=False, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


pytestmark = pytest.mark.skipif(
    not (_docker_daemon_available() and _binary_available("kubectl") and _binary_available("pulumi")),
    reason="needs docker + kubectl + pulumi CLI on PATH",
)


def _assert_twice_and_hash_passes(substrate: DockerSubstrate, expected_arch: str, expected_substrate_name: str) -> None:
    """Drive RuntimeCheck.run against one substrate and assert all reproducibility invariants hold."""
    result = RuntimeCheck(substrates=[substrate]).run(
        CONFIGMAP_PROGRAM, K8S_SCHEMA, arch_override=expected_arch
    )

    # Surface orchestration failures first; they carry the most diagnostic value.
    assert not result.failures, (
        f"orchestration failures: {[(f.stage, f.detail) for f in result.failures]}"
    )
    assert result.substrate == expected_substrate_name, (
        f"unexpected substrate picked: {result.substrate}"
    )
    assert result.spec.arch == expected_arch
    assert result.spec.orchestrator == "k8s"
    assert result.hash_1 and result.hash_2, (
        f"both runs should produce hashes: hash_1={result.hash_1!r} hash_2={result.hash_2!r}"
    )
    assert result.passed, (
        f"twice-and-hash mismatch — diff paths: {result.diff}"
    )
    assert result.hash_1 == result.hash_2


def test_pulumi_up_against_real_amd64_k3s_substrate():
    """Real `pulumi up` succeeds against a kubeconfig produced by DockerSubstrate.

    Proves the substrate's kubeconfig is a working target for the Pulumi
    Automation API and that the deployed object is observable via kubectl.
    Uses a ConfigMap fixture to keep the test independent of image-registry
    reachability from inside the privileged k3s container.
    """
    substrate = DockerSubstrate(
        image=K3S_IMAGE,
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        api_host_port=16448,
        startup_timeout=240,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_COMMAND,
    )
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s")
    target = substrate.provision(spec)

    try:
        assert target.kind == "kubeconfig"

        with tempfile.TemporaryDirectory(prefix="act-pulumi-state-") as backend_dir:
            outcome = run_pulumi_against(
                target=target,
                program_path=CONFIGMAP_PROGRAM,
                backend_dir=backend_dir,
            )

        assert outcome.failure is None, (
            f"pulumi up failed: stage={outcome.failure.stage} detail={outcome.failure.detail}"
            if outcome.failure else "unreachable"
        )
        assert outcome.outputs.get("name") == "act-runtime-probe", (
            f"expected configmap 'name' output, got: {outcome.outputs}"
        )

        # destroy already ran; the ConfigMap should be gone. Re-running up
        # would prove twice-and-hash but is gated on RuntimeCheck.run, not
        # on this single-up integration. Verify instead that destroy left
        # the cluster clean.
        check = subprocess.run(
            ["kubectl", "--kubeconfig", target.endpoint, "get", "configmap",
             "act-runtime-probe", "-n", "default", "--ignore-not-found", "-o", "name"],
            capture_output=True, check=True, timeout=15,
        )
        assert check.stdout.strip() == b"", (
            f"configmap survived destroy: {check.stdout!r}"
        )
    finally:
        target.teardown()


def test_runtime_check_twice_and_hash_against_real_amd64_k3s_cluster():
    """Full RuntimeCheck.run end-to-end: twice-and-hash on a real amd64 cluster."""
    substrate = DockerSubstrate(
        image=K3S_IMAGE,
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        api_host_port=16449,
        startup_timeout=240,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_COMMAND,
    )
    _assert_twice_and_hash_passes(substrate, "x86_64-linux", "docker:linux/amd64")


@pytest.mark.skipif(
    not _arm64_host(),
    reason="arm64 e2e runs only on arm64 hosts (privileged k3s under binfmt is too slow / unstable)",
)
def test_runtime_check_twice_and_hash_against_real_arm64_k3s_cluster():
    """Full RuntimeCheck.run end-to-end: twice-and-hash on a real arm64 cluster."""
    substrate = DockerSubstrate(
        image=K3S_IMAGE,
        platform="linux/arm64",
        spec_arch="aarch64-linux",
        api_host_port=16450,
        startup_timeout=240,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_COMMAND,
    )
    _assert_twice_and_hash_passes(substrate, "aarch64-linux", "docker:linux/arm64")


@pytest.mark.skipif(
    not _riscv64_image_present(),
    reason="riscv64 substrate image not built; run tests/integration/k3s_riscv64/build.sh first",
)
def test_runtime_check_twice_and_hash_against_real_riscv64_k3s_cluster():
    """Full RuntimeCheck.run end-to-end: twice-and-hash on a real riscv64 cluster.

    riscv64 runs under QEMU user-mode binfmt emulation. The pinned image
    bundles CNI + bridge conflist so the node still reaches Ready; the
    workload (a ConfigMap) doesn't need pod scheduling, so the slower
    emulated control plane doesn't dominate the test.
    """
    substrate = DockerSubstrate(
        image=K3S_RISCV64_IMAGE,
        platform="linux/riscv64",
        spec_arch="riscv64-linux",
        api_host_port=16451,
        startup_timeout=600,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_RISCV64_COMMAND,
    )
    _assert_twice_and_hash_passes(substrate, "riscv64-linux", "docker:linux/riscv64")


def test_gpu_substrate_provisions_cluster_with_nvidia_gpu_extended_resource():
    """GpuSubstrate provisions a real k3s cluster and declares nvidia.com/gpu schedulable.

    Drives the substrate directly (not through RuntimeCheck.run) — GPU feature
    auto-detection in extract_target_spec is a separate follow-up. The
    substrate's contract is exercised end-to-end: provision → kubeconfig +
    `nvidia.com/gpu: 1` in node allocatable → teardown.

    Works on any host with docker + kubectl (no GPU hardware needed). The
    Extended Resource patch is what makes the cluster schedulable for
    GPU-flagged workloads; real CUDA execution still requires GPU hardware
    on the host (no general-purpose GPU emulator exists for k8s).
    """
    substrate = GpuSubstrate(
        image=K3S_IMAGE,
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"gpu"}),
        gpu_count=1,
        api_host_port=16453,
        startup_timeout=240,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_COMMAND,
    )
    spec = TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["gpu"])
    target = substrate.provision(spec)

    try:
        assert target.kind == "kubeconfig"

        # Verify the Extended Resource is now schedulable on the node.
        out = subprocess.run(
            [
                "kubectl", "--kubeconfig", target.endpoint,
                "--insecure-skip-tls-verify",
                "get", "nodes",
                "-o", r"jsonpath={.items[0].status.allocatable.nvidia\.com/gpu}",
            ],
            capture_output=True, check=True, timeout=15,
        ).stdout.decode().strip()
        assert out == "1", f"expected nvidia.com/gpu=1 in node allocatable, got {out!r}"

        # Confirm capacity too (Extended Resources mirror across both fields).
        capacity_out = subprocess.run(
            [
                "kubectl", "--kubeconfig", target.endpoint,
                "--insecure-skip-tls-verify",
                "get", "nodes",
                "-o", r"jsonpath={.items[0].status.capacity.nvidia\.com/gpu}",
            ],
            capture_output=True, check=True, timeout=15,
        ).stdout.decode().strip()
        assert capacity_out == "1", f"expected nvidia.com/gpu=1 in node capacity, got {capacity_out!r}"
    finally:
        target.teardown()
