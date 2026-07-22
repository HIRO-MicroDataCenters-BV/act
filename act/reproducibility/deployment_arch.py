"""Smoke-boot every image a Pulumi program references under QEMU for a target arch."""

from __future__ import annotations

from typing import Callable, Literal

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from act.core.mock_generator import MockGenerator

ImageBootReason = Literal["no_arch_variant", "boot_failed", "binfmt_missing", "timeout", "docker_missing"]

# Docker stderr fragments meaning "no image variant for this platform"
# (dockerd/buildkit emit different strings by version); matched lowercase.
_NO_ARCH_VARIANT_FRAGMENTS: tuple[str, ...] = (
    "no matching manifest",
    "manifest unknown",
    "image platform",
    "no matching entries in manifest list",
)

# Foreign-arch binary with no QEMU interpreter registered: a prerequisite gap, not a bad image.
_BINFMT_MISSING_FRAGMENTS: tuple[str, ...] = (
    "exec format error",
    "exec user process caused",
)

# The image pulled for this arch but has no /bin/true (distroless/scratch). Reaching the exec
# stage proves the arch variant exists. Anchor the ENOENT to /bin/true so an unrelated
# "no such file or directory" elsewhere in stderr can't be mistaken for a distroless pass.
_DISTROLESS_ENOENT = re.compile(r"/bin/true.{0,40}(no such file or directory|executable file not found)")


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
    "kubernetes:apps/v1:Deployment": lambda o: _extract_k8s_containers(o, ["spec", "template", "spec", "containers"]),
    "kubernetes:apps/v1:StatefulSet": lambda o: _extract_k8s_containers(o, ["spec", "template", "spec", "containers"]),
    "kubernetes:apps/v1:DaemonSet": lambda o: _extract_k8s_containers(o, ["spec", "template", "spec", "containers"]),
    "kubernetes:batch/v1:Job": lambda o: _extract_k8s_containers(o, ["spec", "template", "spec", "containers"]),
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
                        detail=(
                            "docker binary not found; install docker + run "
                            "`docker run --privileged --rm tonistiigi/binfmt --install all` "
                            "to enable QEMU emulation"
                        ),
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
        stderr_lower = stderr.lower()
        if any(frag in stderr_lower for frag in _NO_ARCH_VARIANT_FRAGMENTS):
            reason: ImageBootReason = "no_arch_variant"
        elif any(frag in stderr_lower for frag in _BINFMT_MISSING_FRAGMENTS):
            reason = "binfmt_missing"
        elif _DISTROLESS_ENOENT.search(stderr_lower):
            # Distroless/scratch: pulled for this arch and reached exec (no /bin/true) -> pass.
            # A broken ELF interpreter looks the same and isn't distinguished (an image bug).
            return None
        else:
            reason = "boot_failed"
        return ImageBootFailure(image=image, reason=reason, detail=stderr)
