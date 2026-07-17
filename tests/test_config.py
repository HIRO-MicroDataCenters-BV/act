import re
from pathlib import Path

import pytest

from act.config import (
    _FILE_TO_ENV,
    DEFAULT_ACV_TIMEOUT_S,
    DEFAULT_K3S_IMAGE,
    DEFAULT_K3S_RISCV64_IMAGE,
    DEFAULT_LOG_LEVEL,
    SUPPORTED_ARCHS,
    ActConfig,
)


def test_example_toml_keys_match_config():
    """Every key in act.example.toml must be a real config field, so the sample can't drift."""
    text = (Path(__file__).parent.parent / "act.example.toml").read_text()
    keys = set(re.findall(r"^#\s*(\w+)\s*=", text, re.MULTILINE))
    valid = set(_FILE_TO_ENV) | {"acv_base_url"}
    assert keys and keys <= valid, f"unknown keys in sample: {keys - valid}"


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


def _write(tmp_path, body):
    path = tmp_path / "act.toml"
    path.write_text(body)
    return str(path)


def test_load_no_file_is_from_env():
    assert ActConfig.load(env={}, config_path=None).log_level == DEFAULT_LOG_LEVEL


def test_load_file_value_applies(tmp_path):
    cfg = ActConfig.load(env={}, config_path=_write(tmp_path, 'log_level = "INFO"\nfuzz_iterations = 200\n'))
    assert (cfg.log_level, cfg.fuzz_iterations) == ("INFO", 200)


def test_load_env_overrides_file(tmp_path):
    cfg = ActConfig.load(env={"ACT_LOG_LEVEL": "DEBUG"}, config_path=_write(tmp_path, 'log_level = "INFO"\n'))
    assert cfg.log_level == "DEBUG"


def test_load_blank_env_still_layers_file(tmp_path):
    # A blank env value must not block the file value and revert to the default.
    cfg = ActConfig.load(env={"ACT_LOG_LEVEL": ""}, config_path=_write(tmp_path, 'log_level = "INFO"\n'))
    assert cfg.log_level == "INFO"


def test_load_cli_precedence_left_to_caller(tmp_path):
    # The file sets INFO; a caller-supplied CLI value would override the loaded cfg upstream.
    cfg = ActConfig.load(env={}, config_path=_write(tmp_path, 'log_level = "INFO"\n'))
    assert cfg.log_level == "INFO"


def test_load_toml_list_and_dict(tmp_path):
    body = 'runtime_archs = ["riscv64", "amd64"]\nacv_extra_body = {chat_template_kwargs = {enable_thinking = false}}\n'
    cfg = ActConfig.load(env={}, config_path=_write(tmp_path, body))
    assert cfg.runtime_archs == ("riscv64", "amd64")
    assert cfg.acv_extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_load_acv_base_url_layers_under_both(tmp_path):
    body = 'acv_base_url = "http://file"\n'
    # CAPE env alias set, ACT var unset -> env alias wins over the file value.
    cfg = ActConfig.load(env={"CAPE_ACV_MODEL_URL": "http://env"}, config_path=_write(tmp_path, body))
    assert cfg.acv_base_url == "http://env"
    # Neither env var set -> file value applies.
    assert ActConfig.load(env={}, config_path=_write(tmp_path, body)).acv_base_url == "http://file"


def test_load_missing_file_falls_back(tmp_path):
    cfg = ActConfig.load(env={"ACT_LOG_LEVEL": "ERROR"}, config_path=str(tmp_path / "nope.toml"))
    assert cfg.log_level == "ERROR"


def test_load_malformed_toml_falls_back(tmp_path):
    cfg = ActConfig.load(env={}, config_path=_write(tmp_path, "this is = = not toml"))
    assert cfg.log_level == DEFAULT_LOG_LEVEL


def test_load_tool_act_table(tmp_path):
    cfg = ActConfig.load(env={}, config_path=_write(tmp_path, '[tool.act]\nlog_level = "INFO"\n'))
    assert cfg.log_level == "INFO"
