"""End-to-end integration: substrate + pulumi up + probe against real k3s.

The mocked tests in test_reproducibility_runtime_check.py cover the
orchestration logic in isolation. This file proves the integration glue
works against a real ephemeral cluster:

  DockerSubstrate.provision (real k3s container) →
    run_pulumi_against (real `pulumi up` via Automation API) →
      probe_k8s (real `kubectl get pods`) →
        teardown.

Scope deliberately stops at a *single* pulumi up. Running pulumi up twice
in the same Python process via the in-process Automation API hits a
known grpc engine race that requires a separate fix (subprocess-based
pulumi up, or LocalWorkspace project mode). The substantive
twice-and-hash claim from D4.2 §2.3.7 stays under the mocked tests until
that race is resolved.

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

REPO_ROOT = Path(__file__).resolve().parent.parent
# ConfigMap-only fixture: no image pulls, no pod scheduling. Image pulls
# inside a privileged k3s container under QEMU emulation routinely exceed
# Pulumi's 10-min default timeout; a ConfigMap exercises the same
# substrate → pulumi up → kubectl path without that bottleneck.
CONFIGMAP_PROGRAM = str(REPO_ROOT / "tests" / "fixtures" / "kubernetes" / "configmap.py")
K8S_SCHEMA = str(REPO_ROOT / "examples" / "kubernetes" / "schema.json")

K3S_IMAGE = os.environ.get("ACT_K3S_IMAGE", "rancher/k3s:v1.32.1-k3s1")
K3S_DOCKER_ARGS = ("--privileged", "--tmpfs", "/run", "--tmpfs", "/var/run")
K3S_COMMAND = (
    "server",
    "--disable=traefik",
    "--write-kubeconfig-mode=644",
    "--snapshotter=native",
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


pytestmark = pytest.mark.skipif(
    not (_docker_daemon_available() and _binary_available("kubectl") and _binary_available("pulumi")),
    reason="needs docker + kubectl + pulumi CLI on PATH",
)


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
    """Full RuntimeCheck.run end-to-end: twice-and-hash on a real cluster.

    Exercises the substantive reproducibility claim from D4.2 §2.3.7
    ("executing the same program twice on the target platform and
    comparing the output hashes") against a real ephemeral k3s cluster.

    Uses a single-substrate registry pinned to amd64 so the test doesn't
    depend on which architectures happen to be available locally.
    """
    substrates = [
        DockerSubstrate(
            image=K3S_IMAGE,
            platform="linux/amd64",
            spec_arch="x86_64-linux",
            api_host_port=16449,
            startup_timeout=240,
            extra_docker_args=K3S_DOCKER_ARGS,
            command=K3S_COMMAND,
        ),
    ]
    result = RuntimeCheck(substrates=substrates).run(CONFIGMAP_PROGRAM, K8S_SCHEMA)

    assert result.substrate == "docker:linux/amd64", (
        f"unexpected substrate picked: {result.substrate}"
    )
    assert result.spec.arch == "x86_64-linux"
    assert result.spec.orchestrator == "k8s"
    assert not result.failures, (
        f"orchestration failures: {[(f.stage, f.detail) for f in result.failures]}"
    )
    assert result.hash_1 and result.hash_2, (
        f"both runs should produce hashes: hash_1={result.hash_1!r} hash_2={result.hash_2!r}"
    )
    assert result.passed, (
        f"twice-and-hash mismatch — diff paths: {result.diff}"
    )
    assert result.hash_1 == result.hash_2
