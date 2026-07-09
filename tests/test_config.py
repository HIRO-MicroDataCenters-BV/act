from act.config import (
    DEFAULT_ACV_TIMEOUT_S,
    DEFAULT_K3S_IMAGE,
    DEFAULT_K3S_RISCV64_IMAGE,
    DEFAULT_LOG_LEVEL,
    SUPPORTED_ARCHS,
    ActConfig,
)


def test_defaults_when_env_empty():
    cfg = ActConfig.from_env(env={})
    assert cfg.acv_model is None
    assert cfg.acv_base_url is None
    assert cfg.acv_api_key is None
    assert cfg.acv_timeout == DEFAULT_ACV_TIMEOUT_S
    assert cfg.acv_mode == "advisory"
    assert cfg.log_level == DEFAULT_LOG_LEVEL
    assert cfg.k3s_image == DEFAULT_K3S_IMAGE
    assert cfg.k3s_riscv64_image == DEFAULT_K3S_RISCV64_IMAGE


def test_base_url_wins_over_cape_alias():
    cfg = ActConfig.from_env(env={"ACT_ACV_BASE_URL": "http://a", "CAPE_ACV_MODEL_URL": "http://b"})
    assert cfg.acv_base_url == "http://a"


def test_cape_alias_used_when_base_url_unset():
    cfg = ActConfig.from_env(env={"CAPE_ACV_MODEL_URL": "http://b"})
    assert cfg.acv_base_url == "http://b"


def test_timeout_parsed_and_bad_value_falls_back():
    assert ActConfig.from_env(env={"ACT_ACV_TIMEOUT": "45"}).acv_timeout == 45.0
    assert ActConfig.from_env(env={"ACT_ACV_TIMEOUT": "nope"}).acv_timeout == DEFAULT_ACV_TIMEOUT_S


def test_mode_and_log_level_validated():
    assert ActConfig.from_env(env={"ACT_ACV_MODE": "blocking"}).acv_mode == "blocking"
    assert ActConfig.from_env(env={"ACT_ACV_MODE": "bogus"}).acv_mode == "advisory"
    assert ActConfig.from_env(env={"ACT_LOG_LEVEL": "DEBUG"}).log_level == "DEBUG"
    assert ActConfig.from_env(env={"ACT_LOG_LEVEL": "bogus"}).log_level == DEFAULT_LOG_LEVEL


def test_image_overrides():
    cfg = ActConfig.from_env(env={"ACT_K3S_IMAGE": "foo/bar:1", "ACT_K3S_RISCV64_IMAGE": "baz/qux:2"})
    assert cfg.k3s_image == "foo/bar:1"
    assert cfg.k3s_riscv64_image == "baz/qux:2"


def test_from_env_reads_os_environ(monkeypatch):
    monkeypatch.setenv("ACT_ACV_MODE", "blocking")
    assert ActConfig.from_env().acv_mode == "blocking"


def test_int_fields_parse_and_fall_back():
    cfg = ActConfig.from_env(env={"ACT_K3S_API_HOST_PORT": "7443", "ACT_ACV_MAX_ITERATIONS": "6"})
    assert cfg.k3s_api_host_port == 7443
    assert cfg.acv_max_iterations == 6
    # bad value -> default
    assert ActConfig.from_env(env={"ACT_ACCELERATOR_COUNT": "x"}).accelerator_count == 1


def test_runtime_archs_subset_and_validation():
    assert ActConfig.from_env(env={}).runtime_archs == SUPPORTED_ARCHS
    # subset, case/space tolerant, invalid entries dropped
    assert ActConfig.from_env(env={"ACT_RUNTIME_ARCHS": "riscv64, AMD64, bogus"}).runtime_archs == ("riscv64", "amd64")
    # all-invalid -> default
    assert ActConfig.from_env(env={"ACT_RUNTIME_ARCHS": "s390x"}).runtime_archs == SUPPORTED_ARCHS


def test_namespace_and_resource_name_overrides():
    cfg = ActConfig.from_env(env={"ACT_K8S_NAMESPACE": "apps", "ACT_K8S_GPU_RESOURCE_NAME": "amd.com/gpu"})
    assert cfg.k8s_namespace == "apps"
    assert cfg.gpu_resource_name == "amd.com/gpu"


def test_timeout_and_path_b_defaults():
    cfg = ActConfig.from_env(env={})
    assert cfg.k3s_startup_timeout_s == 180
    assert cfg.image_boot_timeout_s == 60
    assert cfg.fuzz_iterations == 100
    assert cfg.property_max_examples == 50
