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
K3S_COMMAND = ("server", "--disable=traefik", "--write-kubeconfig-mode=644")


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
