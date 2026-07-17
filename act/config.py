"""Central configuration: every ACT environment variable is read here."""

from __future__ import annotations

from typing import Mapping, Optional, TypeVar

import json
import logging
import os
import tomllib
from dataclasses import dataclass, field

_T = TypeVar("_T")

log = logging.getLogger("act.config")

LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")
ACV_MODES: tuple[str, ...] = ("advisory", "blocking")
SUPPORTED_ARCHS: tuple[str, ...] = ("amd64", "arm64", "riscv64")

DEFAULT_LOG_LEVEL = "WARNING"
DEFAULT_ACV_MODE = "advisory"
DEFAULT_ACV_TIMEOUT_S = 20.0
DEFAULT_ACV_MAX_ITERATIONS = 3
DEFAULT_ACV_MIN_REQUEST_INTERVAL_S = 0.0
DEFAULT_ACV_MAX_RETRIES = 3
DEFAULT_FUZZ_ITERATIONS = 100
DEFAULT_PROPERTY_MAX_EXAMPLES = 50
DEFAULT_EXEC_TIMEOUT_S = 30
SCHEMA_FETCH_MODES: tuple[str, ...] = ("allow", "deny")
DEFAULT_SCHEMA_FETCH = "allow"
DEFAULT_K3S_IMAGE = "rancher/k3s:v1.32.1-k3s1"
DEFAULT_K3S_RISCV64_IMAGE = "ghcr.io/carv-ics-forth/k3s:v1.32.1-k3s1-riscv64"
DEFAULT_K8S_NAMESPACE = "default"
DEFAULT_K3S_API_HOST_PORT = 6443
DEFAULT_K3S_STARTUP_TIMEOUT_S = 180
DEFAULT_IMAGE_BOOT_TIMEOUT_S = 60
DEFAULT_K8S_API_READY_TIMEOUT_S = 60
DEFAULT_K8S_PROBE_TIMEOUT_S = 60
DEFAULT_GPU_RESOURCE_NAME = "nvidia.com/gpu"
DEFAULT_FPGA_RESOURCE_NAME = "cape.eu/fpga"
DEFAULT_CXL_RESOURCE_NAME = "cape.eu/cxl"
DEFAULT_ACCELERATOR_COUNT = 1


@dataclass(frozen=True)
class ActConfig:
    """Environment-derived configuration. Build it with :meth:`from_env`."""

    log_level: str = DEFAULT_LOG_LEVEL

    # Extra rule engines to load (e.g. "checkov"); a CLI --rules overrides this.
    rules: tuple[str, ...] = ()

    # ACT Cognitive Validator (optional feature).
    acv_model: Optional[str] = None
    acv_base_url: Optional[str] = None
    acv_api_key: Optional[str] = None
    acv_timeout: float = DEFAULT_ACV_TIMEOUT_S
    acv_mode: str = DEFAULT_ACV_MODE
    acv_max_iterations: int = DEFAULT_ACV_MAX_ITERATIONS
    # Rate-limit controls for free/quota-limited endpoints (e.g. Gemini free tier).
    acv_min_request_interval_s: float = DEFAULT_ACV_MIN_REQUEST_INTERVAL_S
    acv_max_retries: int = DEFAULT_ACV_MAX_RETRIES
    # Endpoint-specific fields merged into every chat-completions request body, e.g.
    # {"chat_template_kwargs": {"enable_thinking": false}} to turn off Qwen3 thinking.
    acv_extra_body: dict = field(default_factory=dict)

    # Path B (parameterized programs) test depth.
    fuzz_iterations: int = DEFAULT_FUZZ_ITERATIONS
    property_max_examples: int = DEFAULT_PROPERTY_MAX_EXAMPLES

    # Wall-clock cap on running a program under mocks (seconds).
    exec_timeout_s: int = DEFAULT_EXEC_TIMEOUT_S

    # Whether a missing schema may be fetched over the network; deny for offline/hardened runs.
    schema_fetch: str = DEFAULT_SCHEMA_FETCH

    # Reproducibility substrate images.
    k3s_image: str = DEFAULT_K3S_IMAGE
    k3s_riscv64_image: str = DEFAULT_K3S_RISCV64_IMAGE

    # Reproducibility target.
    k8s_namespace: str = DEFAULT_K8S_NAMESPACE
    k3s_api_host_port: int = DEFAULT_K3S_API_HOST_PORT
    runtime_archs: tuple[str, ...] = SUPPORTED_ARCHS

    # Reproducibility timeouts (seconds).
    k3s_startup_timeout_s: int = DEFAULT_K3S_STARTUP_TIMEOUT_S
    image_boot_timeout_s: int = DEFAULT_IMAGE_BOOT_TIMEOUT_S
    k8s_api_ready_timeout_s: int = DEFAULT_K8S_API_READY_TIMEOUT_S
    k8s_probe_timeout_s: int = DEFAULT_K8S_PROBE_TIMEOUT_S

    # Accelerator Extended Resources.
    gpu_resource_name: str = DEFAULT_GPU_RESOURCE_NAME
    fpga_resource_name: str = DEFAULT_FPGA_RESOURCE_NAME
    cxl_resource_name: str = DEFAULT_CXL_RESOURCE_NAME
    accelerator_count: int = DEFAULT_ACCELERATOR_COUNT

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ActConfig":
        """Read the environment; missing, empty, or out-of-range values fall back to defaults."""
        env = os.environ if env is None else env
        return cls(
            log_level=_read_choice(env.get("ACT_LOG_LEVEL"), LOG_LEVELS, DEFAULT_LOG_LEVEL, name="ACT_LOG_LEVEL"),
            rules=_read_str_list(env.get("ACT_RULES")),
            acv_model=_read_str(env.get("ACT_ACV_MODEL"), None),
            acv_base_url=_read_str(env.get("ACT_ACV_BASE_URL"), None) or _read_str(env.get("CAPE_ACV_MODEL_URL"), None),
            acv_api_key=_read_str(env.get("ACT_ACV_API_KEY"), None),
            acv_timeout=_read_float(
                env.get("ACT_ACV_TIMEOUT"), DEFAULT_ACV_TIMEOUT_S, name="ACT_ACV_TIMEOUT", minimum=0.1
            ),
            acv_mode=_read_choice(env.get("ACT_ACV_MODE"), ACV_MODES, DEFAULT_ACV_MODE, name="ACT_ACV_MODE"),
            acv_max_iterations=_read_int(
                env.get("ACT_ACV_MAX_ITERATIONS"), DEFAULT_ACV_MAX_ITERATIONS, name="ACT_ACV_MAX_ITERATIONS", minimum=1
            ),
            acv_min_request_interval_s=_read_float(
                env.get("ACT_ACV_MIN_REQUEST_INTERVAL_S"),
                DEFAULT_ACV_MIN_REQUEST_INTERVAL_S,
                name="ACT_ACV_MIN_REQUEST_INTERVAL_S",
                minimum=0.0,
            ),
            acv_max_retries=_read_int(
                env.get("ACT_ACV_MAX_RETRIES"), DEFAULT_ACV_MAX_RETRIES, name="ACT_ACV_MAX_RETRIES", minimum=0
            ),
            acv_extra_body=_read_json_obj(env.get("ACT_ACV_EXTRA_BODY"), name="ACT_ACV_EXTRA_BODY"),
            fuzz_iterations=_read_int(
                env.get("ACT_FUZZ_ITERATIONS"), DEFAULT_FUZZ_ITERATIONS, name="ACT_FUZZ_ITERATIONS", minimum=1
            ),
            property_max_examples=_read_int(
                env.get("ACT_PROPERTY_MAX_EXAMPLES"),
                DEFAULT_PROPERTY_MAX_EXAMPLES,
                name="ACT_PROPERTY_MAX_EXAMPLES",
                minimum=1,
            ),
            exec_timeout_s=_read_int(
                env.get("ACT_EXEC_TIMEOUT_S"), DEFAULT_EXEC_TIMEOUT_S, name="ACT_EXEC_TIMEOUT_S", minimum=1
            ),
            schema_fetch=_read_choice(
                env.get("ACT_SCHEMA_FETCH"), SCHEMA_FETCH_MODES, DEFAULT_SCHEMA_FETCH, name="ACT_SCHEMA_FETCH"
            ),
            k3s_image=_read_str(env.get("ACT_K3S_IMAGE"), DEFAULT_K3S_IMAGE),
            k3s_riscv64_image=_read_str(env.get("ACT_K3S_RISCV64_IMAGE"), DEFAULT_K3S_RISCV64_IMAGE),
            k8s_namespace=_read_str(env.get("ACT_K8S_NAMESPACE"), DEFAULT_K8S_NAMESPACE),
            k3s_api_host_port=_read_int(
                env.get("ACT_K3S_API_HOST_PORT"),
                DEFAULT_K3S_API_HOST_PORT,
                name="ACT_K3S_API_HOST_PORT",
                minimum=1,
                maximum=65535,
            ),
            runtime_archs=_read_archs(env.get("ACT_RUNTIME_ARCHS"), SUPPORTED_ARCHS),
            k3s_startup_timeout_s=_read_int(
                env.get("ACT_K3S_STARTUP_TIMEOUT_S"),
                DEFAULT_K3S_STARTUP_TIMEOUT_S,
                name="ACT_K3S_STARTUP_TIMEOUT_S",
                minimum=1,
            ),
            image_boot_timeout_s=_read_int(
                env.get("ACT_IMAGE_BOOT_TIMEOUT_S"),
                DEFAULT_IMAGE_BOOT_TIMEOUT_S,
                name="ACT_IMAGE_BOOT_TIMEOUT_S",
                minimum=1,
            ),
            k8s_api_ready_timeout_s=_read_int(
                env.get("ACT_K8S_API_READY_TIMEOUT_S"),
                DEFAULT_K8S_API_READY_TIMEOUT_S,
                name="ACT_K8S_API_READY_TIMEOUT_S",
                minimum=1,
            ),
            k8s_probe_timeout_s=_read_int(
                env.get("ACT_K8S_PROBE_TIMEOUT_S"),
                DEFAULT_K8S_PROBE_TIMEOUT_S,
                name="ACT_K8S_PROBE_TIMEOUT_S",
                minimum=1,
            ),
            gpu_resource_name=_read_str(env.get("ACT_K8S_GPU_RESOURCE_NAME"), DEFAULT_GPU_RESOURCE_NAME),
            fpga_resource_name=_read_str(env.get("ACT_K8S_FPGA_RESOURCE_NAME"), DEFAULT_FPGA_RESOURCE_NAME),
            cxl_resource_name=_read_str(env.get("ACT_K8S_CXL_RESOURCE_NAME"), DEFAULT_CXL_RESOURCE_NAME),
            accelerator_count=_read_int(
                env.get("ACT_ACCELERATOR_COUNT"), DEFAULT_ACCELERATOR_COUNT, name="ACT_ACCELERATOR_COUNT", minimum=1
            ),
        )

    @classmethod
    def load(
        cls,
        env: Optional[Mapping[str, str]] = None,
        config_path: Optional[str] = None,
    ) -> "ActConfig":
        """Compose configuration from the environment and an optional TOML file.

        Precedence is env > file > default, per field. CLI flags override the
        result upstream (the caller applies them). ``from_env`` is reused as-is:
        the file layer is injected as stringified env entries only where the
        matching variable is unset, so every value flows through the same
        validation and an unreadable or missing file falls back cleanly.
        """
        env = os.environ if env is None else env
        toml = _read_toml(config_path)
        if not toml:
            return cls.from_env(env)
        merged = dict(env)

        # A blank/whitespace env value counts as unset, so a file value still layers
        # in rather than the env "" reverting the field to its hardcoded default.
        def _unset(name: str) -> bool:
            return not merged.get(name, "").strip()

        for key, env_name in _FILE_TO_ENV.items():
            if key in toml and _unset(env_name):
                merged[env_name] = _toml_to_env_str(toml[key])
        # acv_base_url is an OR-chain (ACT_ACV_BASE_URL then CAPE_ACV_MODEL_URL);
        # layer the file value under both, never over a set one.
        if "acv_base_url" in toml and _unset("ACT_ACV_BASE_URL") and _unset("CAPE_ACV_MODEL_URL"):
            merged["ACT_ACV_BASE_URL"] = _toml_to_env_str(toml["acv_base_url"])
        return cls.from_env(merged)


def _warn_invalid(name: Optional[str], raw: object, default: object) -> None:
    if name:
        log.warning("config.invalid_value var=%s value=%r ignored; using default %r", name, raw, default)


def _read_str(raw: Optional[str], default: _T) -> "str | _T":
    stripped = raw.strip() if raw else ""
    return stripped if stripped else default


def _read_float(
    raw: Optional[str], default: float, *, name: Optional[str] = None, minimum: Optional[float] = None
) -> float:
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        _warn_invalid(name, raw, default)
        return default
    if minimum is not None and val < minimum:
        _warn_invalid(name, raw, default)
        return default
    return val


def _read_int(
    raw: Optional[str],
    default: int,
    *,
    name: Optional[str] = None,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    if raw is None:
        return default
    try:
        val = int(raw)
    except ValueError:
        _warn_invalid(name, raw, default)
        return default
    if (minimum is not None and val < minimum) or (maximum is not None and val > maximum):
        _warn_invalid(name, raw, default)
        return default
    return val


def _read_choice(raw: Optional[str], choices: tuple[str, ...], default: str, *, name: Optional[str] = None) -> str:
    if raw in choices:
        return raw  # type: ignore[return-value]
    if raw:
        _warn_invalid(name, raw, default)
    return default


def _read_str_list(raw: Optional[str]) -> tuple[str, ...]:
    """Parse a comma-separated env value into a tuple; empty/blank -> ()."""
    if not raw:
        return ()
    return tuple(item for item in (p.strip() for p in raw.split(",")) if item)


def _read_archs(raw: Optional[str], default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    picked = tuple(dict.fromkeys(a for a in (p.strip().lower() for p in raw.split(",")) if a in SUPPORTED_ARCHS))
    return picked or default


def _read_json_obj(raw: Optional[str], *, name: Optional[str] = None) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        _warn_invalid(name, raw, {})
        return {}
    if not isinstance(val, dict):
        _warn_invalid(name, raw, {})
        return {}
    return val


# TOML key -> the env var it feeds when that var is unset. acv_base_url is handled
# separately (OR-chain) in ActConfig.load.
_FILE_TO_ENV: dict[str, str] = {
    "log_level": "ACT_LOG_LEVEL",
    "rules": "ACT_RULES",
    "acv_model": "ACT_ACV_MODEL",
    "acv_api_key": "ACT_ACV_API_KEY",
    "acv_timeout": "ACT_ACV_TIMEOUT",
    "acv_mode": "ACT_ACV_MODE",
    "acv_max_iterations": "ACT_ACV_MAX_ITERATIONS",
    "acv_min_request_interval_s": "ACT_ACV_MIN_REQUEST_INTERVAL_S",
    "acv_max_retries": "ACT_ACV_MAX_RETRIES",
    "acv_extra_body": "ACT_ACV_EXTRA_BODY",
    "fuzz_iterations": "ACT_FUZZ_ITERATIONS",
    "property_max_examples": "ACT_PROPERTY_MAX_EXAMPLES",
    "exec_timeout_s": "ACT_EXEC_TIMEOUT_S",
    "schema_fetch": "ACT_SCHEMA_FETCH",
    "k3s_image": "ACT_K3S_IMAGE",
    "k3s_riscv64_image": "ACT_K3S_RISCV64_IMAGE",
    "k8s_namespace": "ACT_K8S_NAMESPACE",
    "k3s_api_host_port": "ACT_K3S_API_HOST_PORT",
    "runtime_archs": "ACT_RUNTIME_ARCHS",
    "k3s_startup_timeout_s": "ACT_K3S_STARTUP_TIMEOUT_S",
    "image_boot_timeout_s": "ACT_IMAGE_BOOT_TIMEOUT_S",
    "k8s_api_ready_timeout_s": "ACT_K8S_API_READY_TIMEOUT_S",
    "k8s_probe_timeout_s": "ACT_K8S_PROBE_TIMEOUT_S",
    "gpu_resource_name": "ACT_K8S_GPU_RESOURCE_NAME",
    "fpga_resource_name": "ACT_K8S_FPGA_RESOURCE_NAME",
    "cxl_resource_name": "ACT_K8S_CXL_RESOURCE_NAME",
    "accelerator_count": "ACT_ACCELERATOR_COUNT",
}


def _read_toml(config_path: Optional[str]) -> Mapping[str, object]:
    """Load a TOML config file, tolerating absence or malformed content.

    Accepts top-level keys, an ``[act]`` table, or a ``[tool.act]`` table.
    """
    if not config_path:
        return {}
    try:
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("config.toml_unreadable path=%s error=%s", config_path, exc)
        return {}
    tool = data.get("tool")
    if isinstance(tool, dict) and isinstance(tool.get("act"), dict):
        return tool["act"]
    if isinstance(data.get("act"), dict):
        return data["act"]
    return data


def _toml_to_env_str(value: object) -> str:
    """Render a TOML-typed value into the string form ``from_env`` expects."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)
