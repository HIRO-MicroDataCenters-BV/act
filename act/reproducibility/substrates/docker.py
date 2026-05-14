"""Generic data-driven substrate: runs `docker run --platform linux/<arch> <pinned-image>`,
extracts kubeconfig from the running container, returns a ProvisionedTarget.

Adding a new architecture is one row in the substrate registry. The image's
contents (which kernel, which k3s build, which firmware) are the responsibility
of the image-build pipeline, not this substrate.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from act.reproducibility.substrates.base import (
    ProvisionedTarget,
    Substrate,
    TargetSpec,
)


@dataclass
class DockerSubstrate(Substrate):
    image: str
    platform: str
    spec_arch: str
    features: frozenset[str] = field(default_factory=frozenset)
    api_host_port: int = 6443
    startup_timeout: int = 180
    extra_docker_args: tuple[str, ...] = ()
    command: tuple[str, ...] = ()

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"docker:{self.platform}"

    def is_available(self) -> bool:
        return shutil.which("docker") is not None

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

        subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "--platform", self.platform,
                "--name", container_id,
                "-p", f"{self.api_host_port}:6443",
                *self.extra_docker_args,
                self.image,
                *self.command,
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )

        try:
            self._wait_for_api(container_id, self.api_host_port)

            result = subprocess.run(
                ["docker", "exec", container_id, "cat", "/etc/rancher/k3s/k3s.yaml"],
                capture_output=True,
                check=True,
                timeout=30,
            )
            kubeconfig_text = result.stdout.decode()
            kubeconfig_text = re.sub(
                r"server:\s*https?://[^\s]+",
                f"server: https://127.0.0.1:{self.api_host_port}",
                kubeconfig_text,
            )
            kubeconfig.write_text(kubeconfig_text)
        except Exception:
            subprocess.run(
                ["docker", "stop", container_id],
                capture_output=True,
                check=False,
                timeout=30,
            )
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

    def _wait_for_api(self, container_id: str, port: int) -> None:
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
        raise TimeoutError(
            f"k3s did not produce /etc/rancher/k3s/k3s.yaml within {self.startup_timeout}s"
        )
