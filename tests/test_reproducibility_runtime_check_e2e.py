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
import time
from pathlib import Path

import pytest

from act.reproducibility.runtime_check import (
    RuntimeCheck,
    probe_k8s_with_workload_logs,
    run_pulumi_against,
)
from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.cxl import CxlSubstrate
from act.reproducibility.substrates.docker import DockerSubstrate
from act.reproducibility.substrates.fpga import FpgaSubstrate
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
FPGA_IVERILOG_IMAGE = os.environ.get("ACT_FPGA_IVERILOG_IMAGE", "act-fpga:iverilog")
FPGA_BOOT_FLOW_PROGRAM = str(REPO_ROOT / "tests" / "fixtures" / "kubernetes" / "fpga_boot_flow.py")
CXL_QEMU_IMAGE = os.environ.get("ACT_CXL_QEMU_IMAGE", "act-cxl:qemu")
CXL_BOOT_FLOW_PROGRAM = str(REPO_ROOT / "tests" / "fixtures" / "kubernetes" / "cxl_boot_flow.py")
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
    out = subprocess.run(["uname", "-m"], capture_output=True, check=False, timeout=5).stdout.decode().strip()
    return out in ("arm64", "aarch64")


def _riscv64_image_present() -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", K3S_RISCV64_IMAGE],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _fpga_iverilog_image_present() -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", FPGA_IVERILOG_IMAGE],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _cxl_qemu_image_present() -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", CXL_QEMU_IMAGE],
            capture_output=True,
            check=False,
            timeout=10,
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
    result = RuntimeCheck(substrates=[substrate]).run(CONFIGMAP_PROGRAM, K8S_SCHEMA, arch_override=expected_arch)

    # Surface orchestration failures first; they carry the most diagnostic value.
    assert not result.failures, f"orchestration failures: {[(f.stage, f.detail) for f in result.failures]}"
    assert result.substrate == expected_substrate_name, f"unexpected substrate picked: {result.substrate}"
    assert result.spec.arch == expected_arch
    assert result.spec.orchestrator == "k8s"
    assert (
        result.hash_1 and result.hash_2
    ), f"both runs should produce hashes: hash_1={result.hash_1!r} hash_2={result.hash_2!r}"
    assert result.passed, f"twice-and-hash mismatch - diff paths: {result.diff}"
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
            if outcome.failure
            else "unreachable"
        )
        assert (
            outcome.outputs.get("name") == "act-runtime-probe"
        ), f"expected configmap 'name' output, got: {outcome.outputs}"

        # destroy already ran; the ConfigMap should be gone. Re-running up
        # would prove twice-and-hash but is gated on RuntimeCheck.run, not
        # on this single-up integration. Verify instead that destroy left
        # the cluster clean.
        check = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                target.endpoint,
                "get",
                "configmap",
                "act-runtime-probe",
                "-n",
                "default",
                "--ignore-not-found",
                "-o",
                "name",
            ],
            capture_output=True,
            check=True,
            timeout=15,
        )
        assert check.stdout.strip() == b"", f"configmap survived destroy: {check.stdout!r}"
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

    Drives the substrate directly (not through RuntimeCheck.run) - GPU feature
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
        count=1,
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
        out = (
            subprocess.run(
                [
                    "kubectl",
                    "--kubeconfig",
                    target.endpoint,
                    "--insecure-skip-tls-verify",
                    "get",
                    "nodes",
                    "-o",
                    r"jsonpath={.items[0].status.allocatable.nvidia\.com/gpu}",
                ],
                capture_output=True,
                check=True,
                timeout=15,
            )
            .stdout.decode()
            .strip()
        )
        assert out == "1", f"expected nvidia.com/gpu=1 in node allocatable, got {out!r}"

        # Confirm capacity too (Extended Resources mirror across both fields).
        capacity_out = (
            subprocess.run(
                [
                    "kubectl",
                    "--kubeconfig",
                    target.endpoint,
                    "--insecure-skip-tls-verify",
                    "get",
                    "nodes",
                    "-o",
                    r"jsonpath={.items[0].status.capacity.nvidia\.com/gpu}",
                ],
                capture_output=True,
                check=True,
                timeout=15,
            )
            .stdout.decode()
            .strip()
        )
        assert capacity_out == "1", f"expected nvidia.com/gpu=1 in node capacity, got {capacity_out!r}"
    finally:
        target.teardown()


@pytest.mark.skipif(
    not _fpga_iverilog_image_present(),
    reason="act-fpga:iverilog not built; run tests/integration/fpga/build.sh first",
)
def test_runtime_check_twice_and_hash_against_real_fpga_cluster(monkeypatch):
    """Full RuntimeCheck.run end-to-end on an FPGA boot-flow workload.

    The substrate declares cape.eu/fpga as schedulable; the IaC fixture
    deploys a ConfigMap holding the HDL + a Job that runs iverilog against
    it. The probe captures the Job's stdout (deterministic $display output)
    and includes it in the hashed deployed state. Twice-and-hash verifies
    the boot flow simulation runs reproducibly across two pulumi up runs.
    """
    monkeypatch.setenv("ACT_FPGA_IVERILOG_IMAGE", FPGA_IVERILOG_IMAGE)

    # Use the host's native arch so the k3s sandbox doesn't hit
    # "seccomp is not supported" - that error fires under QEMU emulation
    # because containerd inside the emulated kernel lacks the seccomp
    # filter surface. Native arch avoids it entirely.
    if _arm64_host():
        platform, spec_arch = "linux/arm64", "aarch64-linux"
    else:
        platform, spec_arch = "linux/amd64", "x86_64-linux"

    substrate = FpgaSubstrate(
        image=K3S_IMAGE,
        platform=platform,
        spec_arch=spec_arch,
        features=frozenset({"fpga"}),
        count=1,
        api_host_port=16454,
        startup_timeout=240,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_COMMAND,
    )

    # Inside the k3s container we need the iverilog image accessible. Save+load
    # it into containerd via the host's docker daemon. Provision the substrate
    # first so we have the container ID.
    spec = TargetSpec(arch=spec_arch, orchestrator="k8s", features=["fpga"])
    target = substrate.provision(spec)

    try:
        # Get the k3s container ID from the kubeconfig path's parent dir naming convention,
        # then import the local image into containerd.
        ps = (
            subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    "ancestor=" + K3S_IMAGE,
                    "--filter",
                    "publish=16454",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )
            .stdout.decode()
            .strip()
            .split("\n")[0]
        )
        save = subprocess.run(
            ["docker", "save", FPGA_IVERILOG_IMAGE],
            capture_output=True,
            check=True,
            timeout=60,
        )
        subprocess.run(
            ["docker", "exec", "-i", ps, "ctr", "-n", "k8s.io", "images", "import", "-"],
            input=save.stdout,
            capture_output=True,
            check=True,
            timeout=120,
        )

        # Reuse the already-imaged cluster - pass the probe inside
        # run_pulumi_against so it runs between `up` and `destroy` while
        # the iverilog Job's Pod still exists.
        def probe(t) -> dict:
            return probe_k8s_with_workload_logs(t, timeout=180)

        with tempfile.TemporaryDirectory(prefix="act-pulumi-state-") as backend:
            o1 = run_pulumi_against(target, FPGA_BOOT_FLOW_PROGRAM, backend, probe_fn=probe)
            assert o1.failure is None, f"first up failed: {o1.failure}"
            probed_1 = o1.probed or {}
            o2 = run_pulumi_against(target, FPGA_BOOT_FLOW_PROGRAM, backend, probe_fn=probe)
            assert o2.failure is None, f"second up failed: {o2.failure}"
            probed_2 = o2.probed or {}

        # The iverilog $display lines must appear and match across runs.
        logs_1 = probed_1.get("_act_workload_logs", {})
        logs_2 = probed_2.get("_act_workload_logs", {})
        assert "iverilog-boot-flow" in logs_1, f"no iverilog log captured: {logs_1}"
        assert logs_1["iverilog-boot-flow"] == logs_2["iverilog-boot-flow"], (
            "iverilog output diverged across runs:\n"
            f"run1:\n{logs_1['iverilog-boot-flow']}\n"
            f"run2:\n{logs_2['iverilog-boot-flow']}"
        )
        assert (
            "DONE" in logs_1["iverilog-boot-flow"]
        ), f"expected DONE marker in iverilog output, got:\n{logs_1['iverilog-boot-flow']}"
    finally:
        target.teardown()


def _capture_cxl_guest_output(container_name: str, deadline_s: int = 120) -> str:
    """Run the CXL guest once and capture the `cxl list -v` block."""
    subprocess.run(
        ["docker", "run", "--platform", "linux/amd64", "-d", "--name", container_name, CXL_QEMU_IMAGE],
        capture_output=True,
        check=True,
        timeout=30,
    )
    try:
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            status = (
                subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
                    capture_output=True,
                    check=True,
                    timeout=10,
                )
                .stdout.decode()
                .strip()
            )
            if status == "exited":
                break
            time.sleep(2)
        logs = subprocess.run(
            ["docker", "logs", container_name],
            capture_output=True,
            check=True,
            timeout=15,
        ).stdout.decode()
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=30)

    # Extract just the `cxl list -v` block - strips boot banner + dmesg noise
    # so the hash is over the substantive payload.
    start = logs.find("=== cxl list -v ===")
    end = logs.find("=== DONE ===")
    assert start >= 0 and end > start, f"cxl list block not found in logs:\n{logs[-2000:]}"
    return logs[start:end]


@pytest.mark.skipif(
    not _cxl_qemu_image_present(),
    reason="act-cxl:qemu not built; run tests/integration/cxl/build.sh first",
)
def test_cxl_substrate_twice_and_hash_against_real_qemu_emulation():
    """Real CXL Type 3 device emulation is deterministic across two runs.

    Drives the workload image directly via docker (without the k3s
    wrapper) because k3s + the amd64 act-cxl:qemu workload pod can't
    coexist under Docker Desktop's Rosetta translation on Apple Silicon
    (containerd hits a "seccomp is not supported" error). The substantive
    claim - real CXL Type 3 emulation produces deterministic topology
    output across runs - is what twice-and-hash needs to verify, and
    that's exactly what this test does end-to-end against the QEMU guest.

    The k3s-wrapped path is what `CxlSubstrate` is built for; that path
    works on native x86_64 hosts and is exercised in CI runners where
    the host arch matches.
    """
    cxl_1 = _capture_cxl_guest_output("act-cxl-r1", deadline_s=120)
    cxl_2 = _capture_cxl_guest_output("act-cxl-r2", deadline_s=120)

    assert cxl_1 == cxl_2, "CXL guest output diverged across runs:\n" f"run1:\n{cxl_1}\n" f"run2:\n{cxl_2}\n"
    assert "decoder0.0" in cxl_1, f"expected CXL decoder0.0 in guest output, got:\n{cxl_1}"
    assert "volatile_capable" in cxl_1, f"expected volatile_capable in topology, got:\n{cxl_1}"


@pytest.mark.skipif(
    not _cxl_qemu_image_present() or _arm64_host(),
    reason=(
        "Full RuntimeCheck path requires a native x86_64 host so containerd "
        "inside k3s can unpack the linux/amd64 act-cxl:qemu image without "
        "Rosetta-related seccomp issues. Built image present + x86_64 host required."
    ),
)
def test_runtime_check_twice_and_hash_against_real_cxl_cluster(monkeypatch):
    """Full RuntimeCheck.run end-to-end on a CXL Type 3 boot-flow workload.

    Runs only on native x86_64 hosts (CI runners). The substrate declares
    cape.eu/cxl as schedulable; the IaC fixture deploys a Job that runs
    qemu-system-x86_64 with a CXL Type 3 memory device, boots a Linux
    6.8 guest, runs `cxl list -v`, and halts. The probe captures the
    guest's serial output and includes it in the hashed deployed state.
    """
    monkeypatch.setenv("ACT_CXL_QEMU_IMAGE", CXL_QEMU_IMAGE)

    # CXL substrate must run k3s on linux/amd64 so containerd can unpack
    # the linux/amd64 act-cxl:qemu image natively. On Apple Silicon this
    # runs under Docker Desktop's Rosetta translation (slower but works).
    platform, spec_arch = "linux/amd64", "x86_64-linux"

    substrate = CxlSubstrate(
        image=K3S_IMAGE,
        platform=platform,
        spec_arch=spec_arch,
        features=frozenset({"cxl"}),
        count=1,
        api_host_port=16455,
        startup_timeout=300,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_COMMAND,
    )

    spec = TargetSpec(arch=spec_arch, orchestrator="k8s", features=["cxl"])
    target = substrate.provision(spec)

    try:
        # Import the linux/amd64 CXL image into the k3s containerd so the
        # Pod can pull it. (The k3s container may be linux/arm64 on Apple
        # Silicon; containerd accepts amd64 images and runs them via
        # Rosetta translation inside Docker Desktop.)
        ps = (
            subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    "ancestor=" + K3S_IMAGE,
                    "--filter",
                    "publish=16455",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )
            .stdout.decode()
            .strip()
            .split("\n")[0]
        )
        save = subprocess.run(
            ["docker", "save", CXL_QEMU_IMAGE],
            capture_output=True,
            check=True,
            timeout=120,
        )
        subprocess.run(
            ["docker", "exec", "-i", ps, "ctr", "-n", "k8s.io", "images", "import", "-"],
            input=save.stdout,
            capture_output=True,
            check=True,
            timeout=180,
        )

        # Probe runs between up and destroy so the workload Pod logs survive.
        def probe(t) -> dict:
            return probe_k8s_with_workload_logs(t, timeout=300)

        with tempfile.TemporaryDirectory(prefix="act-pulumi-state-") as backend:
            o1 = run_pulumi_against(target, CXL_BOOT_FLOW_PROGRAM, backend, probe_fn=probe)
            assert o1.failure is None, f"first up failed: {o1.failure}"
            probed_1 = o1.probed or {}
            o2 = run_pulumi_against(target, CXL_BOOT_FLOW_PROGRAM, backend, probe_fn=probe)
            assert o2.failure is None, f"second up failed: {o2.failure}"
            probed_2 = o2.probed or {}

        logs_1 = probed_1.get("_act_workload_logs", {})
        logs_2 = probed_2.get("_act_workload_logs", {})
        assert "cxl-boot-flow" in logs_1, f"no cxl boot-flow log captured: {logs_1}"
        # The cxl list output must match across runs (deterministic CXL topology).
        assert logs_1["cxl-boot-flow"] == logs_2["cxl-boot-flow"], (
            "CXL guest output diverged across runs:\n"
            f"run1 last lines:\n{logs_1['cxl-boot-flow'][-2000:]}\n"
            f"run2 last lines:\n{logs_2['cxl-boot-flow'][-2000:]}"
        )
        assert (
            "decoder0.0" in logs_1["cxl-boot-flow"]
        ), f"expected CXL decoder0.0 in guest output, got:\n{logs_1['cxl-boot-flow'][-2000:]}"
    finally:
        target.teardown()
