import shutil
import subprocess
from unittest.mock import patch

import pytest

from act.reproducibility import DeploymentArchCheck, DeploymentArchResult
from act.reproducibility.deployment_arch import IMAGE_EXTRACTORS, _extract_k8s_containers

K8S_NGINX_PROGRAM = "tests/fixtures/kubernetes/nginx_deployment.py"


def test_k8s_deployment_extractor_finds_containers():
    outputs = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "nginx:1.25"},
                        {"name": "sidecar", "image": "myapp/logger:0.9.1"},
                    ]
                }
            }
        }
    }
    extractor = IMAGE_EXTRACTORS["kubernetes:apps/v1:Deployment"]
    assert extractor(outputs) == ["nginx:1.25", "myapp/logger:0.9.1"]


def test_k8s_pod_extractor_finds_containers():
    outputs = {"spec": {"containers": [{"name": "app", "image": "redis:7"}]}}
    extractor = IMAGE_EXTRACTORS["kubernetes:core/v1:Pod"]
    assert extractor(outputs) == ["redis:7"]


def test_extractor_returns_empty_for_malformed_outputs():
    assert _extract_k8s_containers({}, ["spec", "containers"]) == []
    assert _extract_k8s_containers({"spec": None}, ["spec", "containers"]) == []
    assert _extract_k8s_containers({"spec": "not-a-dict"}, ["spec", "containers"]) == []
    assert _extract_k8s_containers({"spec": {"containers": "not-a-list"}}, ["spec", "containers"]) == []


def test_extractor_skips_containers_without_image_field():
    outputs = {"spec": {"containers": [{"name": "no-image"}, {"name": "yes", "image": "x:1"}]}}
    assert _extract_k8s_containers(outputs, ["spec", "containers"]) == ["x:1"]


def test_unknown_resource_token_is_skipped(monkeypatch, kubernetes_schema_path):
    """An unknown token in the plan should not crash; that resource is silently skipped."""
    captured: list = []

    def fake_smoke_boot(self, image):
        captured.append(image)
        return None

    monkeypatch.setattr(DeploymentArchCheck, "_smoke_boot", fake_smoke_boot)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)

    result = DeploymentArchCheck("riscv64").run(K8S_NGINX_PROGRAM, kubernetes_schema_path)

    assert isinstance(result, DeploymentArchResult)
    assert result.images_checked == ["nginx:1.25"]
    assert captured == ["nginx:1.25"]
    assert result.passed is True
    assert result.capture_duration_ms > 0
    assert result.unhandled_tokens == []


def test_non_k8s_program_reports_unhandled_tokens(monkeypatch):
    """A CAPE-only program (no K8s resources) should produce empty images_checked
    and a populated unhandled_tokens list naming what was seen."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)

    result = DeploymentArchCheck("riscv64").run(
        "tests/fixtures/cape/path_a_valid.py", "tests/fixtures/cape/schema.json"
    )

    assert result.images_checked == []
    assert result.passed is True
    assert len(result.unhandled_tokens) > 0
    assert any(t.startswith("cape:") for t in result.unhandled_tokens)


@pytest.mark.parametrize(
    "returncode, stderr, is_timeout, expected_reason",
    [
        (125, b"docker: no matching manifest for linux/riscv64 in the manifest list entries", False, "no_arch_variant"),
        (125, b"docker: manifest unknown for linux/riscv64", False, "no_arch_variant"),
        (125, b"image platform (linux/riscv64) does not match the detected host platform", False, "no_arch_variant"),
        (125, b"no matching entries in manifest list", False, "no_arch_variant"),
        (1, b"exec /bin/true: exec format error", False, "binfmt_missing"),  # QEMU not registered
        (127, b'exec: "/bin/true": stat /bin/true: no such file or directory', False, None),  # distroless -> pass
        (1, b"container failed to start: OOM", False, "boot_failed"),
        # /bin/true present but the ENOENT is unrelated + far away -> not a distroless pass.
        (
            1,
            b"/bin/true ran; then failed opening /etc/very/long/unrelated/config/path/file: no such file or directory",
            False,
            "boot_failed",
        ),
        (0, b"", True, "timeout"),
        (0, b"", False, None),  # zero exit -> boots fine
    ],
)
def test_smoke_boot_classification(returncode, stderr, is_timeout, expected_reason):
    check = DeploymentArchCheck("riscv64", timeout=1)
    if is_timeout:
        ctx = patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1))
    else:
        result = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=b"", stderr=stderr)
        ctx = patch("subprocess.run", return_value=result)
    with ctx:
        failure = check._smoke_boot("img:latest")
    if expected_reason is None:
        assert failure is None
    else:
        assert failure is not None
        assert failure.reason == expected_reason
        assert failure.image == "img:latest"


def test_docker_missing_short_circuits(monkeypatch, kubernetes_schema_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = DeploymentArchCheck("riscv64").run(K8S_NGINX_PROGRAM, kubernetes_schema_path)
    assert result.passed is False
    assert result.images_checked == ["nginx:1.25"]
    assert len(result.failures) == 1
    assert result.failures[0].reason == "docker_missing"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return True


def test_real_smoke_boot_passes_for_multiarch_image():
    if not _docker_available():
        pytest.skip("docker daemon not available")
    # ubuntu:24.04 publishes amd64, arm64, riscv64, ppc64le, s390x.
    check = DeploymentArchCheck("riscv64", timeout=180)
    failure = check._smoke_boot("ubuntu:24.04")
    assert failure is None, f"expected ubuntu:24.04/riscv64 to boot, got: {failure}"


def test_real_smoke_boot_flags_amd64_only_image():
    if not _docker_available():
        pytest.skip("docker daemon not available")
    # A representative amd64-only tag - adjust if it gains other arches.
    check = DeploymentArchCheck("riscv64", timeout=30)
    failure = check._smoke_boot("amd64/alpine:3.19")
    assert failure is not None
    assert failure.reason == "no_arch_variant"
