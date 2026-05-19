"""Helpers used at image-build time, not at runtime.

The DockerSubstrate (runtime) pulls pinned-digest images. These helpers produce
the images. They're shipped with ACT so a partner CI can rebuild the substrate
images on demand: `nxc_compose.render_k8s_composition(...)` emits a Nix flake
that nxc build can consume, `riscv64_image.GuestImage` / `ensure_image()` cache
upstream cloud images by SHA256 for inclusion in QEMU-system substrate images.
"""

from act.reproducibility.image_helpers.nxc_compose import (
    SUPPORTED_ARCHES,
    SUPPORTED_FLAVOURS,
    render_k8s_composition,
)
from act.reproducibility.image_helpers.riscv64_image import (
    DEFAULT_IMAGE,
    GuestImage,
    QemuLaunchConfig,
    build_qemu_command,
    ensure_image,
    render_cloud_init_meta_data,
    render_cloud_init_user_data,
)

__all__ = [
    "DEFAULT_IMAGE",
    "GuestImage",
    "QemuLaunchConfig",
    "SUPPORTED_ARCHES",
    "SUPPORTED_FLAVOURS",
    "build_qemu_command",
    "ensure_image",
    "render_cloud_init_meta_data",
    "render_cloud_init_user_data",
    "render_k8s_composition",
]
