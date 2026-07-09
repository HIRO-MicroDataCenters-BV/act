import pytest

from act.config import (
    DEFAULT_ACV_TIMEOUT_S,
    DEFAULT_K3S_IMAGE,
    DEFAULT_K3S_RISCV64_IMAGE,
    DEFAULT_LOG_LEVEL,
    SUPPORTED_ARCHS,
    ActConfig,
)


def test_defaults():
    cfg = ActConfig.from_env(env={})
    assert (cfg.acv_model, cfg.acv_base_url, cfg.acv_api_key) == (None, None, None)
    assert cfg.acv_timeout == DEFAULT_ACV_TIMEOUT_S
    assert cfg.acv_mode == "advisory"
    assert cfg.log_level == DEFAULT_LOG_LEVEL
    assert cfg.k3s_image == DEFAULT_K3S_IMAGE
    assert cfg.k3s_riscv64_image == DEFAULT_K3S_RISCV64_IMAGE
    assert cfg.runtime_archs == SUPPORTED_ARCHS
    assert (cfg.k3s_startup_timeout_s, cfg.image_boot_timeout_s) == (180, 60)
    assert (cfg.fuzz_iterations, cfg.property_max_examples) == (100, 50)


@pytest.mark.parametrize(
    "env, field, expected",
    [
        # ACV endpoint: base_url wins over CAPE alias; blank falls through / stays None
        ({"ACT_ACV_BASE_URL": "http://a", "CAPE_ACV_MODEL_URL": "http://b"}, "acv_base_url", "http://a"),
        ({"CAPE_ACV_MODEL_URL": "http://b"}, "acv_base_url", "http://b"),
        ({"ACT_ACV_BASE_URL": "", "CAPE_ACV_MODEL_URL": "http://x"}, "acv_base_url", "http://x"),
        ({"ACT_ACV_MODEL": ""}, "acv_model", None),
        # timeout: valid / unparseable / zero-out-of-range / sub-second kept
        ({"ACT_ACV_TIMEOUT": "45"}, "acv_timeout", 45.0),
        ({"ACT_ACV_TIMEOUT": "nope"}, "acv_timeout", DEFAULT_ACV_TIMEOUT_S),
        ({"ACT_ACV_TIMEOUT": "0"}, "acv_timeout", DEFAULT_ACV_TIMEOUT_S),
        ({"ACT_ACV_TIMEOUT": "0.5"}, "acv_timeout", 0.5),
        # enums: valid kept, invalid -> default
        ({"ACT_ACV_MODE": "blocking"}, "acv_mode", "blocking"),
        ({"ACT_ACV_MODE": "bogus"}, "acv_mode", "advisory"),
        ({"ACT_LOG_LEVEL": "DEBUG"}, "log_level", "DEBUG"),
        ({"ACT_LOG_LEVEL": "bogus"}, "log_level", DEFAULT_LOG_LEVEL),
        # strings: override / blank+whitespace -> default / surrounding whitespace stripped
        ({"ACT_K3S_IMAGE": "foo/bar:1"}, "k3s_image", "foo/bar:1"),
        ({"ACT_K3S_RISCV64_IMAGE": "baz/qux:2"}, "k3s_riscv64_image", "baz/qux:2"),
        ({"ACT_K3S_IMAGE": ""}, "k3s_image", DEFAULT_K3S_IMAGE),
        ({"ACT_K8S_NAMESPACE": "apps"}, "k8s_namespace", "apps"),
        ({"ACT_K8S_NAMESPACE": "  "}, "k8s_namespace", "default"),
        ({"ACT_K8S_NAMESPACE": "  apps  "}, "k8s_namespace", "apps"),
        ({"ACT_ACV_MODEL": "  gpt  "}, "acv_model", "gpt"),
        ({"ACT_K8S_GPU_RESOURCE_NAME": "amd.com/gpu"}, "gpu_resource_name", "amd.com/gpu"),
        ({"ACT_K8S_GPU_RESOURCE_NAME": ""}, "gpu_resource_name", "nvidia.com/gpu"),
        # ints: valid / boundary kept / unparseable / out-of-range -> default
        ({"ACT_K3S_API_HOST_PORT": "7443"}, "k3s_api_host_port", 7443),
        ({"ACT_K3S_API_HOST_PORT": "65535"}, "k3s_api_host_port", 65535),
        ({"ACT_K3S_API_HOST_PORT": "99999"}, "k3s_api_host_port", 6443),
        ({"ACT_ACV_MAX_ITERATIONS": "6"}, "acv_max_iterations", 6),
        ({"ACT_ACCELERATOR_COUNT": "x"}, "accelerator_count", 1),
        ({"ACT_ACCELERATOR_COUNT": "0"}, "accelerator_count", 1),
        ({"ACT_K8S_PROBE_TIMEOUT_S": "-1"}, "k8s_probe_timeout_s", 60),
        ({"ACT_K3S_STARTUP_TIMEOUT_S": "0"}, "k3s_startup_timeout_s", 180),
        ({"ACT_ACV_MIN_REQUEST_INTERVAL_S": "-2"}, "acv_min_request_interval_s", 0.0),
        ({"ACT_ACV_MIN_REQUEST_INTERVAL_S": "4.5"}, "acv_min_request_interval_s", 4.5),
        # runtime archs: subset (case/space tolerant, invalid dropped) / all-invalid -> default / dedup
        ({"ACT_RUNTIME_ARCHS": "riscv64, AMD64, bogus"}, "runtime_archs", ("riscv64", "amd64")),
        ({"ACT_RUNTIME_ARCHS": "s390x"}, "runtime_archs", SUPPORTED_ARCHS),
        ({"ACT_RUNTIME_ARCHS": "amd64, AMD64, riscv64, amd64"}, "runtime_archs", ("amd64", "riscv64")),
    ],
)
def test_env_override(env, field, expected):
    assert getattr(ActConfig.from_env(env=env), field) == expected


def test_from_env_reads_os_environ(monkeypatch):
    monkeypatch.setenv("ACT_ACV_MODE", "blocking")
    assert ActConfig.from_env().acv_mode == "blocking"
