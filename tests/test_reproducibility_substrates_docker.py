import shutil
import subprocess

import pytest

from act.reproducibility.substrates._extended_resource import _wait_for_node
from act.reproducibility.substrates.base import TargetSpec
from act.reproducibility.substrates.docker import _ACT_LABEL, DockerSubstrate, reap_orphan_containers


@pytest.fixture
def amd64_substrate() -> DockerSubstrate:
    return DockerSubstrate(
        image="ghcr.io/example/k3s-amd64:latest",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
    )


@pytest.fixture
def riscv64_substrate() -> DockerSubstrate:
    return DockerSubstrate(
        image="ghcr.io/example/k3s-riscv64:latest",
        platform="linux/riscv64",
        spec_arch="riscv64-linux",
    )


def test_substrate_name_includes_platform(amd64_substrate, riscv64_substrate):
    # Two DockerSubstrate instances must be distinguishable in logs/artefacts.
    assert amd64_substrate.name == "docker:linux/amd64"
    assert riscv64_substrate.name == "docker:linux/riscv64"


def test_is_available_when_all_tools_on_path(monkeypatch, amd64_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert amd64_substrate.is_available() is True


def test_is_available_false_when_docker_missing(monkeypatch, amd64_substrate):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert amd64_substrate.is_available() is False


@pytest.mark.parametrize("missing", ["docker", "pulumi", "kubectl"])
def test_is_available_false_when_any_tool_missing(monkeypatch, amd64_substrate, missing):
    # The runtime check needs docker + pulumi + kubectl; any one missing -> skip, not hard-fail.
    monkeypatch.setattr(shutil, "which", lambda name: None if name == missing else f"/usr/bin/{name}")
    assert amd64_substrate.is_available() is False


def test_matches_when_spec_arch_and_orchestrator_align(amd64_substrate):
    assert amd64_substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s")) is True


def test_does_not_match_when_arch_differs(amd64_substrate):
    assert amd64_substrate.matches(TargetSpec(arch="riscv64-linux", orchestrator="k8s")) is False


def test_does_not_match_when_orchestrator_is_none(amd64_substrate):
    assert amd64_substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator=None)) is False


def test_does_not_match_when_spec_features_outside_substrate_features():
    sub = DockerSubstrate(
        image="ghcr.io/example/cxl:latest",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"cxl"}),
    )
    # Substrate offers cxl; spec asks for fpga → no match.
    assert sub.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["fpga"])) is False


def test_matches_when_substrate_features_superset_of_spec_features():
    sub = DockerSubstrate(
        image="ghcr.io/example/cxl:latest",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        features=frozenset({"cxl"}),
    )
    # Spec asks for cxl, substrate offers cxl → match.
    assert sub.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=["cxl"])) is True


def test_matches_when_spec_has_no_features_but_substrate_has(amd64_substrate):
    # An amd64 spec without features should match the plain amd64 substrate.
    assert amd64_substrate.matches(TargetSpec(arch="x86_64-linux", orchestrator="k8s", features=[])) is True


def _setup_provision_mocks(monkeypatch, tmp_path):
    """Common subprocess stubs for provision tests. Returns the call log."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "exec"]:
            # docker exec ... cat /etc/rancher/k3s/k3s.yaml
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=b"apiVersion: v1\nkind: Config\nclusters:\n- cluster:\n    server: https://127.0.0.1:6443\n",
                stderr=b"",
            )
        if cmd[:3] == ["docker", "run", "-d"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"act-container-id\n", stderr=b"")
        if cmd[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"127.0.0.1:54321\n", stderr=b"")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.tempfile.mkdtemp",
        lambda **kw: str(tmp_path),
    )
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.DockerSubstrate._wait_for_api",
        lambda self, container_id: None,
    )
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker._wait_for_node",
        lambda kubeconfig, timeout: "node-1",
    )
    return calls


def test_provision_runs_container_with_platform_and_pinned_image(monkeypatch, tmp_path, amd64_substrate):
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    target = amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    run_calls = [c for c in calls if c[:3] == ["docker", "run", "-d"]]
    assert len(run_calls) == 1
    assert "--platform" in run_calls[0]
    assert run_calls[0][run_calls[0].index("--platform") + 1] == "linux/amd64"
    assert amd64_substrate.image in run_calls[0]
    assert target.kind == "kubeconfig"


def test_provision_returns_kubeconfig_with_rewritten_server_url(monkeypatch, tmp_path, amd64_substrate):
    _setup_provision_mocks(monkeypatch, tmp_path)
    target = amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    with open(target.endpoint) as f:
        kubeconfig = f.read()
    # Substrate's contract: rewrite the server URL to the host-forwarded port.
    assert "127.0.0.1:" in kubeconfig


def test_provision_teardown_stops_container(monkeypatch, tmp_path, amd64_substrate):
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    target = amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    pre_teardown = list(calls)
    target.teardown()
    post_teardown = calls[len(pre_teardown) :]

    stop_calls = [c for c in post_teardown if c[:2] == ["docker", "stop"]]
    assert len(stop_calls) == 1


def test_provision_propagates_docker_run_failure(monkeypatch, tmp_path, amd64_substrate):
    def fake_run(cmd, *args, **kwargs):
        if cmd[:3] == ["docker", "run", "-d"]:
            raise subprocess.CalledProcessError(125, cmd, stderr=b"docker: error")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.tempfile.mkdtemp",
        lambda **kw: str(tmp_path),
    )
    with pytest.raises(subprocess.CalledProcessError):
        amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))


def test_provision_cleans_up_work_dir_on_early_docker_run_failure(monkeypatch, tmp_path, amd64_substrate):
    """The temp work_dir must be removed even when 'docker run' itself fails."""
    work_dir = tmp_path / "act-work"
    work_dir.mkdir()

    def fake_run(cmd, *args, **kwargs):
        if cmd[:3] == ["docker", "run", "-d"]:
            raise subprocess.CalledProcessError(125, cmd, stderr=b"docker: error")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.tempfile.mkdtemp",
        lambda **kw: str(work_dir),
    )

    with pytest.raises(subprocess.CalledProcessError):
        amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    assert not work_dir.exists(), "work_dir was leaked after docker run failure"


def test_provision_cleans_up_work_dir_on_late_failure(monkeypatch, tmp_path, amd64_substrate):
    """A failure after the container is up must stop the container AND remove work_dir."""
    work_dir = tmp_path / "act-work"
    work_dir.mkdir()
    stop_calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        if cmd[:3] == ["docker", "run", "-d"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        if cmd[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"127.0.0.1:54321\n", stderr=b"")
        if cmd[:2] == ["docker", "exec"] and cmd[3:] == ["test", "-f", "/etc/rancher/k3s/k3s.yaml"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        if cmd[:2] == ["docker", "exec"] and "cat" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"kubeconfig read failed")
        if cmd[:2] == ["docker", "stop"]:
            stop_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "act.reproducibility.substrates.docker.tempfile.mkdtemp",
        lambda **kw: str(work_dir),
    )

    with pytest.raises(subprocess.CalledProcessError):
        amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    assert len(stop_calls) == 1, "container was not stopped after late failure"
    assert not work_dir.exists(), "work_dir was leaked after late failure"


def test_provision_passes_extra_docker_args_and_command(monkeypatch, tmp_path):
    sub = DockerSubstrate(
        image="rancher/k3s:v1.32.1-k3s1",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
        extra_docker_args=("--privileged", "--tmpfs", "/run"),
        command=("server", "--disable=traefik"),
    )
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    sub.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    run_call = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
    # Extra args land between the docker-run flags and the image.
    assert "--privileged" in run_call
    assert "--tmpfs" in run_call
    # The command lands AFTER the image.
    image_index = run_call.index(sub.image)
    assert run_call[image_index + 1] == "server"
    assert run_call[image_index + 2] == "--disable=traefik"


def test_extra_docker_args_default_to_empty():
    sub = DockerSubstrate(
        image="x",
        platform="linux/amd64",
        spec_arch="x86_64-linux",
    )
    assert sub.extra_docker_args == ()
    assert sub.command == ()


def test_provision_uses_ephemeral_port_and_resolves_it(monkeypatch, tmp_path, amd64_substrate):
    # Default api_host_port=0 -> publish "-p 6443" (ephemeral) and rewrite the kubeconfig to the
    # docker-assigned host port.
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    target = amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    run_call = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
    assert run_call[run_call.index("-p") + 1] == "127.0.0.1::6443"
    assert any(c[:2] == ["docker", "port"] for c in calls)
    with open(target.endpoint) as f:
        assert "127.0.0.1:54321" in f.read()


def test_provision_fixed_port_skips_resolution(monkeypatch, tmp_path):
    sub = DockerSubstrate(image="x", platform="linux/amd64", spec_arch="x86_64-linux", api_host_port=6443)
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    target = sub.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))

    run_call = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
    assert run_call[run_call.index("-p") + 1] == "127.0.0.1:6443:6443"
    assert not any(c[:2] == ["docker", "port"] for c in calls)  # fixed port needs no lookup
    with open(target.endpoint) as f:
        assert "127.0.0.1:6443" in f.read()


def test_provision_labels_container_for_reaping(monkeypatch, tmp_path, amd64_substrate):
    calls = _setup_provision_mocks(monkeypatch, tmp_path)
    amd64_substrate.provision(TargetSpec(arch="x86_64-linux", orchestrator="k8s"))
    run_call = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
    assert "--label" in run_call
    assert _ACT_LABEL in run_call


def test_reap_orphan_containers_stops_old_skips_young(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr("act.reproducibility.substrates.docker.time.time", lambda: now)
    stopped: list[str] = []

    def fake_run(cmd, *a, **k):
        if cmd[:2] == ["docker", "ps"]:
            out = f"old {now - 2000}\nyoung {now - 10}\n".encode()
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=b"")
        if cmd[:2] == ["docker", "stop"]:
            stopped.append(cmd[2])
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    reap_orphan_containers(max_age_s=1800)
    # Only the container older than max_age is stopped; the fresh one (a live run) is left alone.
    assert stopped == ["old"]


def test_reap_orphan_containers_silent_when_docker_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", boom)
    reap_orphan_containers()  # must not raise


# ----- provision-readiness waits (previously only monkeypatched, never exercised) -----


def test_wait_for_api_returns_when_kubeconfig_present(monkeypatch, amd64_substrate):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
    amd64_substrate._wait_for_api("cid")  # returns without raising


def test_wait_for_api_times_out_when_never_ready():
    sub = DockerSubstrate(image="x", platform="linux/amd64", spec_arch="x86_64-linux", startup_timeout=0)
    with pytest.raises(TimeoutError):
        sub._wait_for_api("cid")


def test_resolve_host_port_parses_first_mapping(monkeypatch, amd64_substrate):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=b"0.0.0.0:32770\n[::]:32770\n")
    )
    assert amd64_substrate._resolve_host_port("cid") == 32770


def test_resolve_host_port_raises_when_no_mapping(monkeypatch, amd64_substrate):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=b""))
    with pytest.raises(RuntimeError):
        amd64_substrate._resolve_host_port("cid")


def test_resolve_host_port_parses_ipv6_first_line(monkeypatch, amd64_substrate):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=b"[::]:32770\n0.0.0.0:32770\n")
    )
    assert amd64_substrate._resolve_host_port("cid") == 32770


def test_wait_for_node_returns_registered_node(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=b"node-1", stderr=b"")
    )
    assert _wait_for_node("/kube.config", timeout=5) == "node-1"


def test_wait_for_node_times_out():
    with pytest.raises(TimeoutError):
        _wait_for_node("/kube.config", timeout=0)
