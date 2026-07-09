"""Central configuration: every ACT environment variable is read here."""

from __future__ import annotations

from typing import Mapping, Optional

import os
from dataclasses import dataclass

LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")
ACV_MODES: tuple[str, ...] = ("advisory", "blocking")
SUPPORTED_ARCHS: tuple[str, ...] = ("amd64", "arm64", "riscv64")

DEFAULT_LOG_LEVEL = "WARNING"
DEFAULT_ACV_MODE = "advisory"
DEFAULT_ACV_TIMEOUT_S = 20.0
DEFAULT_ACV_MAX_ITERATIONS = 3
DEFAULT_K3S_IMAGE = "rancher/k3s:v1.32.1-k3s1"
DEFAULT_K3S_RISCV64_IMAGE = "ghcr.io/carv-ics-forth/k3s:v1.32.1-k3s1-riscv64"


@dataclass(frozen=True)
class ActConfig:
    """Environment-derived configuration. Build it with :meth:`from_env`."""

    log_level: str = DEFAULT_LOG_LEVEL

    # ACT Cognitive Validator (optional feature).
    acv_model: Optional[str] = None
    acv_base_url: Optional[str] = None
    acv_api_key: Optional[str] = None
    acv_timeout: float = DEFAULT_ACV_TIMEOUT_S
    acv_mode: str = DEFAULT_ACV_MODE
    acv_max_iterations: int = DEFAULT_ACV_MAX_ITERATIONS
    # Rate-limit controls for free/quota-limited endpoints (e.g. Gemini free tier).
    acv_min_request_interval_s: float = 0.0
    acv_max_retries: int = 3

    # Path B (parameterized programs) test depth.
    fuzz_iterations: int = 100
    property_max_examples: int = 50

    # Reproducibility substrate images.
    k3s_image: str = DEFAULT_K3S_IMAGE
    k3s_riscv64_image: str = DEFAULT_K3S_RISCV64_IMAGE

    # Reproducibility target.
    k8s_namespace: str = "default"
    k3s_api_host_port: int = 6443
    runtime_archs: tuple[str, ...] = SUPPORTED_ARCHS

    # Reproducibility timeouts (seconds).
    k3s_startup_timeout_s: int = 180
    image_boot_timeout_s: int = 60
    k8s_api_ready_timeout_s: int = 60
    k8s_probe_timeout_s: int = 60

    # Accelerator Extended Resources.
    gpu_resource_name: str = "nvidia.com/gpu"
    fpga_resource_name: str = "cape.eu/fpga"
    cxl_resource_name: str = "cape.eu/cxl"
    accelerator_count: int = 1

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ActConfig":
        """Read the environment; invalid enums/numbers fall back to defaults."""
        env = os.environ if env is None else env
        return cls(
            log_level=_read_choice(env.get("ACT_LOG_LEVEL"), LOG_LEVELS, DEFAULT_LOG_LEVEL),
            acv_model=env.get("ACT_ACV_MODEL"),
            acv_base_url=env.get("ACT_ACV_BASE_URL") or env.get("CAPE_ACV_MODEL_URL"),
            acv_api_key=env.get("ACT_ACV_API_KEY"),
            acv_timeout=_read_float(env.get("ACT_ACV_TIMEOUT"), DEFAULT_ACV_TIMEOUT_S),
            acv_mode=_read_choice(env.get("ACT_ACV_MODE"), ACV_MODES, DEFAULT_ACV_MODE),
            acv_max_iterations=_read_int(env.get("ACT_ACV_MAX_ITERATIONS"), DEFAULT_ACV_MAX_ITERATIONS),
            acv_min_request_interval_s=_read_float(env.get("ACT_ACV_MIN_REQUEST_INTERVAL_S"), 0.0),
            acv_max_retries=_read_int(env.get("ACT_ACV_MAX_RETRIES"), 3),
            fuzz_iterations=_read_int(env.get("ACT_FUZZ_ITERATIONS"), 100),
            property_max_examples=_read_int(env.get("ACT_PROPERTY_MAX_EXAMPLES"), 50),
            k3s_image=env.get("ACT_K3S_IMAGE", DEFAULT_K3S_IMAGE),
            k3s_riscv64_image=env.get("ACT_K3S_RISCV64_IMAGE", DEFAULT_K3S_RISCV64_IMAGE),
            k8s_namespace=env.get("ACT_K8S_NAMESPACE", "default"),
            k3s_api_host_port=_read_int(env.get("ACT_K3S_API_HOST_PORT"), 6443),
            runtime_archs=_read_archs(env.get("ACT_RUNTIME_ARCHS"), SUPPORTED_ARCHS),
            k3s_startup_timeout_s=_read_int(env.get("ACT_K3S_STARTUP_TIMEOUT_S"), 180),
            image_boot_timeout_s=_read_int(env.get("ACT_IMAGE_BOOT_TIMEOUT_S"), 60),
            k8s_api_ready_timeout_s=_read_int(env.get("ACT_K8S_API_READY_TIMEOUT_S"), 60),
            k8s_probe_timeout_s=_read_int(env.get("ACT_K8S_PROBE_TIMEOUT_S"), 60),
            gpu_resource_name=env.get("ACT_K8S_GPU_RESOURCE_NAME", "nvidia.com/gpu"),
            fpga_resource_name=env.get("ACT_K8S_FPGA_RESOURCE_NAME", "cape.eu/fpga"),
            cxl_resource_name=env.get("ACT_K8S_CXL_RESOURCE_NAME", "cape.eu/cxl"),
            accelerator_count=_read_int(env.get("ACT_ACCELERATOR_COUNT"), 1),
        )


def _read_float(raw: Optional[str], default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_int(raw: Optional[str], default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_choice(raw: Optional[str], choices: tuple[str, ...], default: str) -> str:
    return raw if raw in choices else default


def _read_archs(raw: Optional[str], default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    picked = tuple(a for a in (p.strip().lower() for p in raw.split(",")) if a in SUPPORTED_ARCHS)
    return picked or default
