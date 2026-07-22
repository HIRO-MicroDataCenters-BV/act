"""Data-driven substrate: `docker run --platform linux/<arch> <image>`, extract kubeconfig, return a ProvisionedTarget.

A new arch is one registry row; image contents are the image-build pipeline's job, not this substrate's.
"""

from __future__ import annotations

from typing import ClassVar

import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from act.reproducibility.substrates._extended_resource import _wait_for_node
from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)

# Every ACT-spawned cluster carries this label so orphans (left by a killed run) can be reaped.
_ACT_LABEL = "act.reproducibility.substrate=docker"
_CREATED_LABEL = "act.reproducibility.created"
_CREATE_TIMEOUT_S = 300  # bound on `docker run -d` (image pull + start), separate from k3s boot


def reap_orphan_containers(max_age_s: float = 1800) -> None:
    """Best-effort: stop ACT-labelled containers older than max_age_s, left behind by a killed
    run. Age-gated (via the creation-epoch label) so a concurrent run's fresh container is never
    touched. Silent no-op if docker is absent."""
    try:
        listed = subprocess.run(
            ["docker", "ps", "--filter", f"label={_ACT_LABEL}", "--format", '{{.ID}} {{.Label "%s"}}' % _CREATED_LABEL],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return
    now = time.time()
    for line in listed.stdout.decode().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        cid, created = parts
        try:
            if now - float(created) <= max_age_s:
                continue
        except ValueError:
            continue
        subprocess.run(["docker", "stop", cid], capture_output=True, check=False, timeout=30)


@dataclass
class DockerSubstrate(Substrate):
    image: str
    platform: str
    spec_arch: str
    features: frozenset[str] = field(default_factory=frozenset)
    api_host_port: int = 0  # 0 = ephemeral host port (docker assigns; avoids fixed-6443 collision)
    startup_timeout: int = 180
    api_ready_timeout: int = 60
    extra_docker_args: tuple[str, ...] = ()
    command: tuple[str, ...] = ()

    # The runtime check needs all three on PATH: docker (provision the cluster), pulumi
    # (deploy), kubectl (probe). A missing tool makes this substrate unavailable so the
    # check skips honestly rather than hard-failing the gate.
    _REQUIRED_TOOLS: ClassVar[tuple[str, ...]] = ("docker", "pulumi", "kubectl")

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"docker:{self.platform}"

    def is_available(self) -> bool:
        return all(shutil.which(tool) is not None for tool in self._REQUIRED_TOOLS)

    def matches(self, spec: TargetSpec) -> bool:
        if spec.arch != self.spec_arch:
            return False
        if spec.orchestrator != "k8s":
            return False
        if not self.features.issuperset(spec.features):
            return False
        return True

    def provision(self, spec: TargetSpec) -> ProvisionedTarget:
        work_dir = Path(tempfile.mkdtemp(prefix="act-docker-"))
        container_id = "act-" + uuid.uuid4().hex[:8]
        kubeconfig = work_dir / "kubeconfig.yaml"

        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--platform",
                    self.platform,
                    "--name",
                    container_id,
                    "--label",
                    _ACT_LABEL,
                    "--label",
                    f"{_CREATED_LABEL}={int(time.time())}",
                    "-p",
                    # 0 -> let docker assign an ephemeral host port (no fixed-6443 collision).
                    f"{self.api_host_port}:6443" if self.api_host_port else "6443",
                    *self.extra_docker_args,
                    self.image,
                    *self.command,
                ],
                capture_output=True,
                check=True,
                # `docker run -d` returns once the container starts (image pull is the slow part),
                # so bound it independently of the k3s-boot wait in _wait_for_api.
                timeout=_CREATE_TIMEOUT_S,
            )
            host_port = self.api_host_port or self._resolve_host_port(container_id)

            self._wait_for_api(container_id)

            result = subprocess.run(
                ["docker", "exec", container_id, "cat", "/etc/rancher/k3s/k3s.yaml"],
                capture_output=True,
                check=True,
                timeout=30,
            )
            kubeconfig_text = result.stdout.decode()
            kubeconfig_text = re.sub(
                r"server:\s*https?://[^\s]+",
                f"server: https://127.0.0.1:{host_port}",
                kubeconfig_text,
            )
            kubeconfig.write_text(kubeconfig_text)
            # The kubeconfig file exists before the API server can serve requests; wait for a
            # registered node so `pulumi up` doesn't hit an unready API (matters on slow QEMU archs).
            _wait_for_node(str(kubeconfig), self.api_ready_timeout)
        except Exception:
            # `--name` reserves the container, so a run that timed out mid-create may still be up.
            subprocess.run(
                ["docker", "stop", container_id],
                capture_output=True,
                check=False,
                timeout=30,
            )
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

        def teardown() -> None:
            subprocess.run(
                ["docker", "stop", container_id],
                capture_output=True,
                check=False,
                timeout=30,
            )
            shutil.rmtree(work_dir, ignore_errors=True)

        return ProvisionedTarget(
            endpoint=str(kubeconfig),
            kind="kubeconfig",
            teardown=teardown,
        )

    def _resolve_host_port(self, container_id: str) -> int:
        """The ephemeral host port docker mapped to the container's 6443 (e.g. '0.0.0.0:54321')."""
        result = subprocess.run(
            ["docker", "port", container_id, "6443/tcp"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        lines = result.stdout.decode().splitlines()
        if not lines:
            raise RuntimeError(f"docker did not publish a host port for {container_id}:6443")
        return int(lines[0].rsplit(":", 1)[1])

    def _wait_for_api(self, container_id: str) -> None:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            check = subprocess.run(
                ["docker", "exec", container_id, "test", "-f", "/etc/rancher/k3s/k3s.yaml"],
                capture_output=True,
                check=False,
                timeout=5,
            )
            if check.returncode == 0:
                return
            time.sleep(1)
        raise TimeoutError(f"k3s did not produce /etc/rancher/k3s/k3s.yaml within {self.startup_timeout}s")
