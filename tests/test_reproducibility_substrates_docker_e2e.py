"""End-to-end test: DockerSubstrate provisions a real k3s cluster and the
returned kubeconfig is usable by kubectl.

Runs the substrate against upstream rancher/k3s images for amd64 and (on hosts
with binfmt registered) arm64. Each test:
  1. Spins up the container with `--privileged` + the k3s server command.
  2. Waits for /etc/rancher/k3s/k3s.yaml to materialise.
  3. Extracts the kubeconfig, rewrites the API server URL.
  4. Calls `kubectl --kubeconfig=<path> get nodes` and asserts a Ready node.
  5. Tears down.

Skipped when docker/kubectl aren't available so unit-only `pytest` runs stay
fast on contributor machines.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.docker import DockerSubstrate

K3S_IMAGE = os.environ.get("ACT_K3S_IMAGE", "rancher/k3s:v1.32.1-k3s1")
K3S_DOCKER_ARGS = ("--privileged", "--tmpfs", "/run", "--tmpfs", "/var/run")
K3S_COMMAND = (
    "server",
    "--disable=traefik",
    "--write-kubeconfig-mode=644",
    "--snapshotter=native",
)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return True


def _kubectl_available() -> bool:
    return shutil.which("kubectl") is not None


pytestmark = pytest.mark.skipif(
    not _docker_available() or not _kubectl_available(),
    reason="docker + kubectl required for substrate e2e",
)


def _wait_for_ready_node(kubeconfig: str, deadline_seconds: int = 180) -> str:
    deadline = time.monotonic() + deadline_seconds
    last_output = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["kubectl", "--kubeconfig", kubeconfig, "--insecure-skip-tls-verify",
             "get", "nodes", "--no-headers"],
            capture_output=True,
            check=False,
            timeout=15,
        )
        last_output = (result.stdout + result.stderr).decode()
        if result.returncode == 0 and " Ready " in last_output:
            return last_output
        time.sleep(3)
    raise TimeoutError(f"no Ready node within {deadline_seconds}s:\n{last_output}")


def _e2e_run(platform: str, spec_arch: str, host_port: int) -> None:
    sub = DockerSubstrate(
        image=K3S_IMAGE,
        platform=platform,
        spec_arch=spec_arch,
        api_host_port=host_port,
        startup_timeout=240,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=K3S_COMMAND,
    )
    spec = TargetSpec(arch=spec_arch, orchestrator="k8s")
    target = sub.provision(spec)
    try:
        assert target.kind == "kubeconfig"
        out = _wait_for_ready_node(target.endpoint)
        assert " Ready " in out
    finally:
        target.teardown()


def test_e2e_amd64_k3s_cluster_provisions_and_serves_kubeconfig():
    _e2e_run("linux/amd64", "x86_64-linux", host_port=16443)


@pytest.mark.skipif(
    subprocess.run(["uname", "-m"], capture_output=True, check=False, timeout=5)
    .stdout.decode().strip() not in ("arm64", "aarch64"),
    reason="arm64 e2e runs only on arm64 hosts (otherwise binfmt + privileged k3s is too slow / unstable)",
)
def test_e2e_arm64_k3s_cluster_provisions_and_serves_kubeconfig():
    _e2e_run("linux/arm64", "aarch64-linux", host_port=16444)


def _riscv64_image_present() -> bool:
    """Check whether the riscv64 substrate image has been built locally.

    The image is large (~650MB unpacked) and slow to build under QEMU emulation
    (~2 min on Apple Silicon). We don't build it inside the test — it's a one-off
    via `tests/integration/k3s_riscv64/build.sh`. Test skips if not present.
    """
    image = os.environ.get("ACT_K3S_RISCV64_IMAGE", "act-k3s:riscv64")
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, check=False, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


# riscv64 under QEMU user-mode binfmt emulation can't run iptables-dependent
# components reliably (kube-proxy crashes, flannel depends on kube-proxy).
# We disable both, which means no CNI is installed and the node will *register*
# but never reach Ready. The substrate's contract (return a kubeconfig pointing
# at a reachable API server) is still met — Ready transition needs native
# riscv64 silicon or a non-iptables CNI we don't ship today.
_K3S_RISCV64_COMMAND = (
    "server",
    "--disable=traefik",
    "--write-kubeconfig-mode=644",
    "--snapshotter=native",
    "--disable-kube-proxy",
    "--flannel-backend=none",
    "--disable-network-policy",
)


@pytest.mark.skipif(
    not _riscv64_image_present(),
    reason="riscv64 substrate image not built; run tests/integration/k3s_riscv64/build.sh first",
)
def test_e2e_riscv64_k3s_cluster_provisions_and_serves_kubeconfig():
    """Verify the riscv64 substrate satisfies its contract under QEMU emulation.

    Asserts:
      - provision returns a kubeconfig path
      - the API server is reachable via kubectl (a node row appears)

    Does NOT assert the node reaches Ready: that requires a CNI, but the only
    CNI shipped by k3s (flannel) needs iptables, which isn't usable under
    user-mode QEMU emulation. On native riscv64 silicon the same image runs
    with the default `K3S_COMMAND` and the node does reach Ready — out of
    scope for laptop CI.
    """
    image = os.environ.get("ACT_K3S_RISCV64_IMAGE", "act-k3s:riscv64")
    sub = DockerSubstrate(
        image=image,
        platform="linux/riscv64",
        spec_arch="riscv64-linux",
        api_host_port=16445,
        startup_timeout=600,
        extra_docker_args=K3S_DOCKER_ARGS,
        command=_K3S_RISCV64_COMMAND,
    )
    spec = TargetSpec(arch="riscv64-linux", orchestrator="k8s")
    target = sub.provision(spec)
    try:
        assert target.kind == "kubeconfig"
        deadline = time.monotonic() + 300
        last_output = ""
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["kubectl", "--kubeconfig", target.endpoint,
                 "--insecure-skip-tls-verify", "get", "nodes", "--no-headers"],
                capture_output=True, check=False, timeout=15,
            )
            last_output = (result.stdout + result.stderr).decode()
            if result.returncode == 0 and "control-plane" in last_output:
                break
            time.sleep(3)
        else:
            raise TimeoutError(
                f"node row never appeared via kubectl:\n{last_output}"
            )
        assert "control-plane" in last_output, last_output
    finally:
        target.teardown()
