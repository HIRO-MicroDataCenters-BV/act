"""Image-build-time helpers (not used at runtime).

DockerSubstrate pulls pinned-digest images at runtime; these helpers produce them, shipped so CI can rebuild
substrate images on demand. `nxc_compose.render_k8s_composition` emits a Nix flake for `nxc build`;
`riscv64_image.ensure_image` caches upstream cloud images by SHA256 for QEMU substrate images.
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
