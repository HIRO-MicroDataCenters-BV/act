import hashlib
import shutil
from pathlib import Path

import pytest

from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.qemu_riscv64 import (
    DEFAULT_IMAGE,
    GuestImage,
    QemuRiscv64Substrate,
    ensure_image,
    render_cloud_init_user_data,
    render_cloud_init_meta_data,
)


@pytest.fixture
def substrate() -> QemuRiscv64Substrate:
    return QemuRiscv64Substrate()


def test_substrate_name(substrate):
    assert substrate.name == "qemu-riscv64"


def test_is_available_when_qemu_system_riscv64_on_path(monkeypatch, substrate):
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/bin/qemu-system-riscv64" if name == "qemu-system-riscv64" else None,
    )
    assert substrate.is_available() is True


def test_is_available_false_when_qemu_missing(monkeypatch, substrate):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert substrate.is_available() is False


def test_matches_riscv64_k8s(substrate):
    assert substrate.matches(TargetSpec(arch="riscv64-linux", orchestrator="k8s")) is True


def test_does_not_match_x86_64(substrate):
    assert substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s")) is False


def test_does_not_match_aarch64(substrate):
    assert substrate.matches(TargetSpec(arch="aarch64-linux", orchestrator="k8s")) is False


def test_does_not_match_when_orchestrator_is_none(substrate):
    assert substrate.matches(TargetSpec(arch="riscv64-linux", orchestrator=None)) is False


def test_does_not_match_when_features_include_cxl(substrate):
    spec = TargetSpec(arch="riscv64-linux", orchestrator="k8s", features=["cxl"])
    assert substrate.matches(spec) is False


# ---- Image catalogue --------------------------------------------------------


def test_default_image_is_pinned():
    assert DEFAULT_IMAGE.url.startswith("https://")
    assert len(DEFAULT_IMAGE.sha256) == 64
    assert DEFAULT_IMAGE.filename.endswith(".img") or DEFAULT_IMAGE.filename.endswith(".qcow2")


def test_default_image_has_kernel_metadata():
    # Real bring-up needs an OpenSBI firmware path inside the image or alongside it;
    # the GuestImage dataclass surfaces this so the launcher doesn't hardcode paths.
    assert DEFAULT_IMAGE.machine == "virt"
    assert DEFAULT_IMAGE.distro in {"ubuntu", "debian"}


def test_ensure_image_returns_cached_path_when_sha_matches(tmp_path, monkeypatch):
    image = GuestImage(
        url="https://example.com/ignored.img",
        sha256=hashlib.sha256(b"hello").hexdigest(),
        filename="cached.img",
        machine="virt",
        distro="debian",
    )
    cached = tmp_path / image.filename
    cached.write_bytes(b"hello")

    downloads: list = []

    def fake_urlretrieve(url, dest, *args, **kwargs):  # pragma: no cover
        downloads.append((url, dest))
        Path(dest).write_bytes(b"hello")
        return dest, None

    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.urllib.request.urlretrieve",
        fake_urlretrieve,
    )

    resolved = ensure_image(image, cache_dir=tmp_path)
    assert resolved == cached
    assert downloads == []


def test_ensure_image_downloads_when_missing(tmp_path, monkeypatch):
    image = GuestImage(
        url="https://example.com/some.img",
        sha256=hashlib.sha256(b"hello").hexdigest(),
        filename="some.img",
        machine="virt",
        distro="debian",
    )

    def fake_urlretrieve(url, dest, *args, **kwargs):
        Path(dest).write_bytes(b"hello")
        return dest, None

    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.urllib.request.urlretrieve",
        fake_urlretrieve,
    )

    resolved = ensure_image(image, cache_dir=tmp_path)
    assert resolved.read_bytes() == b"hello"


def test_ensure_image_rejects_sha_mismatch(tmp_path, monkeypatch):
    image = GuestImage(
        url="https://example.com/tampered.img",
        sha256="0" * 64,  # not the hash of "hello"
        filename="tampered.img",
        machine="virt",
        distro="debian",
    )

    def fake_urlretrieve(url, dest, *args, **kwargs):
        Path(dest).write_bytes(b"hello")
        return dest, None

    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.urllib.request.urlretrieve",
        fake_urlretrieve,
    )

    with pytest.raises(RuntimeError, match="sha256"):
        ensure_image(image, cache_dir=tmp_path)


# ---- Cloud-init seed --------------------------------------------------------


def test_user_data_starts_with_cloud_config_header():
    rendered = render_cloud_init_user_data(
        ssh_authorized_key="ssh-ed25519 AAAA...key act-test",
        k3s_tarball_url="https://example.com/k3s-riscv64.tar.gz",
        k3s_tarball_sha256="abc" * 21 + "d",  # 64 chars
    )
    assert rendered.splitlines()[0] == "#cloud-config"


def test_user_data_pins_ssh_authorized_key():
    rendered = render_cloud_init_user_data(
        ssh_authorized_key="ssh-ed25519 AAAA... mykey",
        k3s_tarball_url="https://example.com/k3s",
        k3s_tarball_sha256="a" * 64,
    )
    assert "ssh-ed25519 AAAA... mykey" in rendered
    # Must land under a known cloud-init schema key, not as a free comment.
    assert "ssh_authorized_keys" in rendered


def test_user_data_installs_k3s_with_pinned_sha():
    sha = "deadbeef" * 8
    rendered = render_cloud_init_user_data(
        ssh_authorized_key="ssh-ed25519 KEY",
        k3s_tarball_url="https://example.com/k3s.tar.gz",
        k3s_tarball_sha256=sha,
    )
    assert "https://example.com/k3s.tar.gz" in rendered
    assert sha in rendered
    # Must verify the tarball before installing.
    assert "sha256sum" in rendered or "sha256" in rendered


def test_user_data_rejects_bogus_sha():
    with pytest.raises(ValueError, match="sha256"):
        render_cloud_init_user_data(
            ssh_authorized_key="ssh-ed25519 KEY",
            k3s_tarball_url="https://example.com/k3s",
            k3s_tarball_sha256="too-short",
        )


def test_meta_data_has_instance_id_and_hostname():
    rendered = render_cloud_init_meta_data(instance_id="act-001", hostname="act-riscv64")
    assert "instance-id: act-001" in rendered
    assert "local-hostname: act-riscv64" in rendered
