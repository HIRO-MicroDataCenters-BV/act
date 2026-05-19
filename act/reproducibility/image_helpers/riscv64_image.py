"""Helpers for building riscv64 substrate images.

Used by the image-build CI to fetch a pinned Ubuntu/Debian cloud image, render
a cloud-init seed for first-boot k3s install (CARV-ICS-FORTH fork), and
assemble the qemu-system-riscv64 launch command that becomes the substrate
image's entrypoint. Not used at runtime by DockerSubstrate.
"""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuestImage:
    url: str
    sha256: str
    filename: str
    machine: str
    distro: str


DEFAULT_IMAGE = GuestImage(
    url="https://cloud-images.ubuntu.com/releases/noble/release-20260401/ubuntu-24.04-server-cloudimg-riscv64.img",
    sha256="0" * 64,
    filename="ubuntu-24.04-server-cloudimg-riscv64.img",
    machine="virt",
    distro="ubuntu",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class QemuLaunchConfig:
    disk_path: Path
    seed_iso_path: Path
    ssh_host_port: int
    api_host_port: int
    memory_mib: int
    cpus: int


def build_qemu_command(cfg: QemuLaunchConfig) -> list[str]:
    netdev = (
        "user,id=net0,"
        f"hostfwd=tcp::{cfg.ssh_host_port}-:22,"
        f"hostfwd=tcp::{cfg.api_host_port}-:6443"
    )
    return [
        "qemu-system-riscv64",
        "-M", "virt",
        "-cpu", "rv64",
        "-smp", str(cfg.cpus),
        "-m", str(cfg.memory_mib),
        "-nographic",
        "-bios", "default",
        "-kernel", "default",
        "-drive", f"file={cfg.disk_path},format=qcow2,if=virtio",
        "-drive", f"file={cfg.seed_iso_path},format=raw,if=virtio",
        "-device", "virtio-net-device,netdev=net0",
        "-netdev", netdev,
    ]


_USER_DATA_TEMPLATE = """\
#cloud-config
hostname: act-riscv64
users:
  - name: act
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - {ssh_authorized_key}
ssh_pwauth: false

write_files:
  - path: /usr/local/bin/install-k3s.sh
    permissions: '0755'
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      curl -fsSL -o /tmp/k3s.tar.gz {k3s_tarball_url}
      echo "{k3s_tarball_sha256}  /tmp/k3s.tar.gz" | sha256sum -c -
      tar -xzf /tmp/k3s.tar.gz -C /usr/local/bin/
      chmod +x /usr/local/bin/k3s
      /usr/local/bin/k3s server \\
        --disable=traefik \\
        --write-kubeconfig-mode=644 \\
        --write-kubeconfig=/etc/rancher/k3s/k3s.yaml &

runcmd:
  - /usr/local/bin/install-k3s.sh
"""


def render_cloud_init_user_data(
    *, ssh_authorized_key: str, k3s_tarball_url: str, k3s_tarball_sha256: str
) -> str:
    if len(k3s_tarball_sha256) != 64 or not all(
        c in "0123456789abcdef" for c in k3s_tarball_sha256.lower()
    ):
        raise ValueError(
            f"k3s_tarball_sha256 must be a 64-char hex digest; got {k3s_tarball_sha256!r}"
        )
    return _USER_DATA_TEMPLATE.format(
        ssh_authorized_key=ssh_authorized_key,
        k3s_tarball_url=k3s_tarball_url,
        k3s_tarball_sha256=k3s_tarball_sha256,
    )


def render_cloud_init_meta_data(*, instance_id: str, hostname: str) -> str:
    return f"instance-id: {instance_id}\nlocal-hostname: {hostname}\n"


def ensure_image(image: GuestImage, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / image.filename

    if target.exists() and _sha256_file(target) == image.sha256:
        return target

    urllib.request.urlretrieve(image.url, str(target))
    digest = _sha256_file(target)
    if digest != image.sha256:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"sha256 mismatch for {image.filename}: expected {image.sha256}, got {digest}"
        )
    return target
