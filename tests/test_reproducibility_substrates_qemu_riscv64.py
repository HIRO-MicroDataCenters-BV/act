import hashlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.qemu_riscv64 import (
    DEFAULT_IMAGE,
    GuestImage,
    QemuLaunchConfig,
    QemuRiscv64Substrate,
    build_qemu_command,
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


# ---- QEMU launcher ----------------------------------------------------------


def test_build_qemu_command_uses_virt_machine():
    cfg = QemuLaunchConfig(
        disk_path=Path("/tmp/disk.img"),
        seed_iso_path=Path("/tmp/seed.iso"),
        ssh_host_port=2222,
        api_host_port=6443,
        memory_mib=4096,
        cpus=4,
    )
    cmd = build_qemu_command(cfg)

    assert cmd[0] == "qemu-system-riscv64"
    assert "-M" in cmd and cmd[cmd.index("-M") + 1] == "virt"


def test_build_qemu_command_forwards_ssh_and_api_ports():
    cfg = QemuLaunchConfig(
        disk_path=Path("/tmp/disk.img"),
        seed_iso_path=Path("/tmp/seed.iso"),
        ssh_host_port=2222,
        api_host_port=6443,
        memory_mib=4096,
        cpus=4,
    )
    cmd = build_qemu_command(cfg)
    netdev_args = " ".join(cmd)
    assert "hostfwd=tcp::2222-:22" in netdev_args
    assert "hostfwd=tcp::6443-:6443" in netdev_args


def test_build_qemu_command_attaches_disk_and_seed_iso():
    cfg = QemuLaunchConfig(
        disk_path=Path("/tmp/disk.img"),
        seed_iso_path=Path("/tmp/seed.iso"),
        ssh_host_port=2222,
        api_host_port=6443,
        memory_mib=4096,
        cpus=4,
    )
    cmd = build_qemu_command(cfg)
    joined = " ".join(cmd)
    assert "/tmp/disk.img" in joined
    assert "/tmp/seed.iso" in joined


def test_build_qemu_command_passes_memory_and_cpus():
    cfg = QemuLaunchConfig(
        disk_path=Path("/tmp/disk.img"),
        seed_iso_path=Path("/tmp/seed.iso"),
        ssh_host_port=2222,
        api_host_port=6443,
        memory_mib=8192,
        cpus=8,
    )
    cmd = build_qemu_command(cfg)
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "8192"
    assert "-smp" in cmd and cmd[cmd.index("-smp") + 1] == "8"


# ---- provision + teardown ---------------------------------------------------


@pytest.fixture
def riscv64_spec() -> TargetSpec:
    return TargetSpec(arch="riscv64-linux", orchestrator="k8s")


def _stub_qemu_pipeline(monkeypatch, tmp_path, calls):
    """Stubs out ensure_image, genisoimage, Popen, scp, sed. Tests inject call observation."""
    image_path = tmp_path / "image.img"
    image_path.write_bytes(b"img")

    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.ensure_image",
        lambda image, cache_dir: image_path,
    )

    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.shutil.which",
        lambda name: "/usr/bin/" + name,
    )

    popen_instance = MagicMock()
    popen_instance.poll.return_value = None
    popen_instance.pid = 12345

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        # If asked to fetch kubeconfig (scp ... kubeconfig.yaml), drop a fake file.
        if cmd[0] == "scp":
            dest = Path(cmd[-1])
            dest.write_text("apiVersion: v1\nkind: Config\nclusters:\n- name: act\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    def fake_popen(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return popen_instance

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return popen_instance


def test_provision_invokes_qemu_and_builds_seed_iso(monkeypatch, tmp_path, riscv64_spec, substrate):
    calls: list[list[str]] = []
    _stub_qemu_pipeline(monkeypatch, tmp_path, calls)
    monkeypatch.setenv("ACT_RISCV_SSH_PUBKEY", "ssh-ed25519 AAAA test@act")
    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.tempfile.mkdtemp",
        lambda **kw: str(tmp_path),
    )

    target = substrate.provision(riscv64_spec)

    qemu_calls = [c for c in calls if c[0] == "qemu-system-riscv64"]
    iso_calls = [c for c in calls if Path(c[0]).name in {"genisoimage", "mkisofs", "xorriso"}]
    assert len(qemu_calls) == 1
    assert len(iso_calls) == 1
    assert target.kind == "kubeconfig"
    assert target.endpoint.endswith("kubeconfig.yaml")


def test_provision_returns_kubeconfig_with_rewritten_server_url(monkeypatch, tmp_path, riscv64_spec, substrate):
    calls: list[list[str]] = []
    _stub_qemu_pipeline(monkeypatch, tmp_path, calls)
    monkeypatch.setenv("ACT_RISCV_SSH_PUBKEY", "ssh-ed25519 AAAA test@act")
    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.tempfile.mkdtemp",
        lambda **kw: str(tmp_path),
    )

    target = substrate.provision(riscv64_spec)

    kubeconfig = Path(target.endpoint).read_text()
    # The substrate rewrites the API server URL to the host-forwarded port.
    assert "127.0.0.1:" in kubeconfig or "localhost:" in kubeconfig


def test_provision_raises_when_ssh_pubkey_missing(monkeypatch, tmp_path, riscv64_spec, substrate):
    monkeypatch.delenv("ACT_RISCV_SSH_PUBKEY", raising=False)
    with pytest.raises(RuntimeError, match="ACT_RISCV_SSH_PUBKEY"):
        substrate.provision(riscv64_spec)


def test_teardown_kills_qemu_process(monkeypatch, tmp_path, riscv64_spec, substrate):
    calls: list[list[str]] = []
    popen_instance = _stub_qemu_pipeline(monkeypatch, tmp_path, calls)
    monkeypatch.setenv("ACT_RISCV_SSH_PUBKEY", "ssh-ed25519 AAAA test@act")
    monkeypatch.setattr(
        "act.reproducibility.substrates.qemu_riscv64.tempfile.mkdtemp",
        lambda **kw: str(tmp_path),
    )

    target = substrate.provision(riscv64_spec)
    target.teardown()

    assert popen_instance.terminate.called or popen_instance.kill.called
