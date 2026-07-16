"""`act doctor`: report availability of the optional external tools ACT's deeper
checks rely on, and map each opt-in flag to its prerequisites. Read-only."""

from __future__ import annotations

from typing import Optional

import shutil
from importlib.util import find_spec
from pathlib import Path

from act.config import ActConfig


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _qemu_binfmt() -> Optional[bool]:
    """True/False if Linux binfmt_misc has a qemu-* handler; None when not
    checkable (non-Linux, or binfmt_misc unmounted). Docker may still register
    QEMU on demand, so None is not a hard failure."""
    binfmt = Path("/proc/sys/fs/binfmt_misc")
    if not binfmt.is_dir():
        return None
    try:
        return any(p.name.startswith("qemu-") for p in binfmt.iterdir())
    except OSError:
        return None


def _acv_extra_installed() -> bool:
    return find_spec("langgraph") is not None and find_spec("langchain_core") is not None


def _yesno(present: bool) -> str:
    return "yes" if present else "no"


def _tool_status(present: Optional[bool]) -> str:
    if present is None:
        return "unknown"
    return "ok" if present else "missing"


def _readiness(missing: list[str]) -> str:
    return "ready" if not missing else "needs " + ", ".join(missing)


def run(cfg: Optional[ActConfig] = None) -> int:
    cfg = ActConfig.from_env() if cfg is None else cfg

    docker = _which("docker")
    kubectl = _which("kubectl")
    pulumi = _which("pulumi")
    qemu = _qemu_binfmt()
    acv_extra = _acv_extra_installed()
    acv_model = bool(cfg.acv_model)
    acv_url = bool(cfg.acv_base_url)

    # qemu counts as missing only when explicitly absent on Linux; unknown does not block.
    arch_missing = [n for n, ok in (("docker", docker), ("qemu binfmt", qemu is not False)) if not ok]
    runtime_missing = [n for n, ok in (("docker", docker), ("kubectl", kubectl), ("pulumi", pulumi)) if not ok]
    acv_missing = [
        n for n, ok in (("acv extra", acv_extra), ("ACT_ACV_MODEL", acv_model), ("ACT_ACV_BASE_URL", acv_url)) if not ok
    ]

    lines = [
        "ACT preflight",
        "",
        "external tools",
        f"  docker       {_tool_status(docker)}",
        f"  kubectl      {_tool_status(kubectl)}",
        f"  pulumi       {_tool_status(pulumi)}",
        f"  qemu binfmt  {_tool_status(qemu)}",
        "",
        "cognitive validator (acv)",
        f"  acv extra installed   {_yesno(acv_extra)}",
        f"  ACT_ACV_MODEL set     {_yesno(acv_model)}",
        f"  ACT_ACV_BASE_URL set  {_yesno(acv_url)}",
        "",
        "optional flags and their prerequisites",
        f"  --check-deployment-arch     docker + qemu binfmt         -> {_readiness(arch_missing)}",
        f"  --check-deployment-runtime  docker + kubectl + pulumi    -> {_readiness(runtime_missing)}",
        f"  --acv-mode blocking         acv extra + ACV env vars     -> {_readiness(acv_missing)}",
        "  --rules checkov             bundled                      -> ready",
        "",
        "Run 'act check --help' for all options.",
    ]
    print("\n".join(lines))
    return 0
