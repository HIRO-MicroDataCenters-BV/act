"""Smoke-boot every image referenced by a Pulumi program under QEMU for a target arch.

Answers: "Do all images this deployment references actually start under linux/<arch>?"
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from act.core.mock_generator import MockGenerator

ImageBootReason = Literal["no_arch_variant", "boot_failed", "timeout", "docker_missing"]


@dataclass
class ImageBootFailure:
    image: str
    reason: ImageBootReason
    detail: str


@dataclass
class DeploymentArchResult:
    passed: bool
    arch: str
    images_checked: list[str] = field(default_factory=list)
    failures: list[ImageBootFailure] = field(default_factory=list)
    capture_duration_ms: int = 0
    unhandled_tokens: list[str] = field(default_factory=list)


def _extract_k8s_containers(outputs: dict, container_path: list[str]) -> list[str]:
    node: object = outputs
    for key in container_path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
        if node is None:
            return []
    if not isinstance(node, list):
        return []
    images = []
    for container in node:
        if isinstance(container, dict) and isinstance(container.get("image"), str):
            images.append(container["image"])
    return images


IMAGE_EXTRACTORS: dict[str, Callable[[dict], list[str]]] = {
    "kubernetes:core/v1:Pod": lambda o: _extract_k8s_containers(o, ["spec", "containers"]),
    "kubernetes:apps/v1:Deployment": lambda o: _extract_k8s_containers(
        o, ["spec", "template", "spec", "containers"]
    ),
    "kubernetes:apps/v1:StatefulSet": lambda o: _extract_k8s_containers(
        o, ["spec", "template", "spec", "containers"]
    ),
    "kubernetes:apps/v1:DaemonSet": lambda o: _extract_k8s_containers(
        o, ["spec", "template", "spec", "containers"]
    ),
    "kubernetes:batch/v1:Job": lambda o: _extract_k8s_containers(
        o, ["spec", "template", "spec", "containers"]
    ),
}


class DeploymentArchCheck:
    def __init__(self, arch: str, timeout: int = 60):
        self._arch = arch
        self._timeout = timeout

    def run(self, program_path: str, schema_path) -> DeploymentArchResult:
        schemas = [schema_path] if isinstance(schema_path, str) else list(schema_path)
        start = time.monotonic_ns()
        mg = MockGenerator(schemas)
        plan = mg.run_with_mocks(program_path)
        images, unhandled_tokens = self._extract_images(plan, mg)
        duration_ms = int((time.monotonic_ns() - start) // 1_000_000)

        if shutil.which("docker") is None:
            return DeploymentArchResult(
                passed=False,
                arch=self._arch,
                images_checked=images,
                failures=[
                    ImageBootFailure(
                        image=img,
                        reason="docker_missing",
                        detail="docker binary not found; install docker + run `docker run --privileged --rm tonistiigi/binfmt --install all` to enable QEMU emulation",
                    )
                    for img in images
                ],
                capture_duration_ms=duration_ms,
                unhandled_tokens=unhandled_tokens,
            )

        failures = [f for f in (self._smoke_boot(img) for img in images) if f is not None]
        return DeploymentArchResult(
            passed=len(failures) == 0,
            arch=self._arch,
            images_checked=images,
            failures=failures,
            capture_duration_ms=duration_ms,
            unhandled_tokens=unhandled_tokens,
        )

    def _extract_images(self, plan: dict, mg: MockGenerator) -> tuple[list[str], list[str]]:
        seen: set[str] = set()
        ordered: list[str] = []
        unhandled: set[str] = set()
        for resource_name in plan:
            token = mg.get_resource_type(resource_name)
            if not token:
                continue
            extractor = IMAGE_EXTRACTORS.get(token)
            if extractor is None:
                unhandled.add(token)
                continue
            for image in extractor(plan[resource_name]):
                if image not in seen:
                    seen.add(image)
                    ordered.append(image)
        return ordered, sorted(unhandled)

    def _smoke_boot(self, image: str) -> ImageBootFailure | None:
        cmd = [
            "docker",
            "run",
            "--rm",
            "--platform",
            f"linux/{self._arch}",
            "--entrypoint",
            "/bin/true",
            image,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            return ImageBootFailure(image=image, reason="timeout", detail=f">{self._timeout}s")
        if result.returncode == 0:
            return None
        stderr = result.stderr.decode(errors="replace").strip()
        reason: ImageBootReason = (
            "no_arch_variant" if "no matching manifest" in stderr.lower() else "boot_failed"
        )
        return ImageBootFailure(image=image, reason=reason, detail=stderr)
