#!/usr/bin/env python3
"""
ACT — Automated Configuration Testing

Usage:
  python act/run.py --program <path> --schema <path> [<path> ...] [--output <dir>] [--rules checkov]
                    [--check-deployment-arch <arch>]

Exit codes:
  0  all checks passed
  1  one or more violations found
  2  pipeline error
"""

import argparse
import json
import logging
import os
import sys

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.core.pipeline import ACTPipeline
from act.gate.ci_gate import CIGate
from act.integrations.checkov_adapter import load_checkov_rules
from act.reproducibility import (
    DeploymentArchCheck,
    DeploymentArchResult,
    DockerSubstrate,
    FpgaSubstrate,
    GpuSubstrate,
    PlanCheck,
    PlanCheckResult,
    ReproducibilityArtefact,
    RuntimeCheck,
    RuntimeCheckResult,
    write_artefact,
)
from act.rules import auto_load


class _JsonFormatter(logging.Formatter):
    _FIELDS = (
        "program",
        "resources",
        "violations",
        "duration_ms",
        "passed",
        "parameterized",
        "resource_type",
        "fields",
        "exit_code",
        "reason",
        "iterations",
        "count",
        "hash_1",
        "hash_2",
        "diff",
        "image",
        "arch",
        "detail",
        "images_checked",
        "check",
        "capture_duration_ms",
        "artefact_path",
        "unhandled_tokens",
        "substrate",
        "stage",
        "spec",
    )

    def format(self, record: logging.LogRecord) -> str:
        d: dict = {"level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for k in self._FIELDS:
            if hasattr(record, k):
                d[k] = getattr(record, k)
        return json.dumps(d)


def _configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    # Root at ERROR suppresses Pulumi/asyncio noise (direct logging.debug() calls)
    logging.basicConfig(level=logging.ERROR, handlers=[handler], force=True)
    # Explicitly set act.* to the requested level
    logging.getLogger("act").setLevel(level)


def _load_extra_rules(oracle, mg, engines: list) -> None:
    """Load additional rule engines requested via --rules."""
    if "checkov" not in engines:
        return
    # One unscoped rule per provider — avoids schema vs runtime token mismatches.
    providers = {info["token"].split(":")[0] for info in mg._type_map.values()}
    for provider in providers:
        try:
            load_checkov_rules(oracle, check_type=provider)
        except ValueError:
            pass  # no Checkov checks for this provider — skip silently


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="act",
        description="Validate a Pulumi program against security rules without provisioning real infrastructure.",
    )
    parser.add_argument("--program", required=True, help="Path to Pulumi program file or project directory")
    parser.add_argument(
        "--schema",
        required=True,
        nargs="+",
        metavar="SCHEMA",
        help="Path(s) to provider schema JSON. Repeat for multi-provider programs.",
    )
    parser.add_argument("--output", default=None, help="Directory to write run artefacts (optional)")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: WARNING — silent in CI unless set)",
    )
    parser.add_argument(
        "--rules",
        nargs="*",
        default=[],
        metavar="ENGINE",
        help="Extra rule engines to load (e.g. --rules checkov). Repeatable.",
    )
    parser.add_argument(
        "--check-deployment-arch",
        default=None,
        metavar="ARCH",
        help="Smoke-boot every image referenced by the deployment under linux/<ARCH> using QEMU. "
        "Example: --check-deployment-arch riscv64. Requires docker + binfmt_misc.",
    )
    parser.add_argument(
        "--check-deployment-runtime",
        action="store_true",
        help="Spin up an ephemeral target environment via a substrate, run pulumi up "
        "against it twice, hash the probed outputs, and compare. Requires nxc + nix "
        "for the default nixos-compose substrate.",
    )
    return parser


_K3S_IMAGE = os.environ.get("ACT_K3S_IMAGE", "rancher/k3s:v1.32.1-k3s1")
_K3S_RISCV64_IMAGE = os.environ.get(
    "ACT_K3S_RISCV64_IMAGE",
    "ghcr.io/carv-ics-forth/k3s:v1.32.1-k3s1-riscv64",
)
_K3S_DOCKER_ARGS: tuple[str, ...] = ("--privileged", "--tmpfs", "/run", "--tmpfs", "/var/run")
_K3S_COMMAND: tuple[str, ...] = (
    "server",
    "--disable=traefik",
    "--write-kubeconfig-mode=644",
    # `native` snapshotter avoids overlayfs mounts that fail under QEMU
    # binfmt or in some host filesystem layouts. Slightly slower than overlay
    # but reliably works on every substrate platform we ship today.
    "--snapshotter=native",
)

# riscv64 under QEMU user-mode binfmt emulation cannot run iptables-dependent
# components (kube-proxy crashes, flannel depends on kube-proxy). The image
# bundles the reference CNI plugins + a bridge conflist so kubelet still
# satisfies NetworkReady without iptables.
_K3S_RISCV64_COMMAND: tuple[str, ...] = _K3S_COMMAND + (
    "--disable-kube-proxy",
    "--flannel-backend=none",
    "--disable-network-policy",
)


def _default_substrates() -> list:
    """Substrate registry. Each row is a pinned image + platform + arch.

    amd64/arm64 use upstream rancher/k3s (multi-arch, well-tested).
    riscv64 uses the CARV-ICS-FORTH fork that publishes pinned tarballs.
    Both can be overridden via ACT_K3S_IMAGE / ACT_K3S_RISCV64_IMAGE env vars.
    """
    return [
        DockerSubstrate(
            image=_K3S_IMAGE,
            platform="linux/amd64",
            spec_arch="x86_64-linux",
            extra_docker_args=_K3S_DOCKER_ARGS,
            command=_K3S_COMMAND,
        ),
        DockerSubstrate(
            image=_K3S_IMAGE,
            platform="linux/arm64",
            spec_arch="aarch64-linux",
            extra_docker_args=_K3S_DOCKER_ARGS,
            command=_K3S_COMMAND,
        ),
        DockerSubstrate(
            image=_K3S_RISCV64_IMAGE,
            platform="linux/riscv64",
            spec_arch="riscv64-linux",
            extra_docker_args=_K3S_DOCKER_ARGS,
            command=_K3S_RISCV64_COMMAND,
        ),
        # GPU substrate: only matches specs with features=["gpu"], so it
        # doesn't steal non-GPU amd64 work from the regular row above. The
        # post-provision step declares nvidia.com/gpu as a k8s Extended
        # Resource — schedulable without GPU hardware. Real CUDA execution
        # requires a GPU-equipped host; this substrate validates the IaC layer.
        GpuSubstrate(
            image=_K3S_IMAGE,
            platform="linux/amd64",
            spec_arch="x86_64-linux",
            features=frozenset({"gpu"}),
            extra_docker_args=_K3S_DOCKER_ARGS,
            command=_K3S_COMMAND,
        ),
        # FPGA substrate: declares cape.eu/fpga as a schedulable Extended
        # Resource. The boot-flow simulation itself runs inside the user's
        # workload Pod (typically the act-fpga:iverilog image) and its
        # $display output is captured by probe_k8s_with_workload_logs.
        FpgaSubstrate(
            image=_K3S_IMAGE,
            platform="linux/amd64",
            spec_arch="x86_64-linux",
            features=frozenset({"fpga"}),
            extra_docker_args=_K3S_DOCKER_ARGS,
            command=_K3S_COMMAND,
        ),
    ]


def _run_runtime_check(
    program: str, schemas: list[str], log: logging.Logger
) -> RuntimeCheckResult:
    check = RuntimeCheck(substrates=_default_substrates())
    result = check.run(program, schemas)

    substrate_unavailable = any(f.stage == "substrate_unavailable" for f in result.failures)
    spec_unsupported = any(f.stage == "spec_unsupported" for f in result.failures)

    if substrate_unavailable or spec_unsupported:
        log.warning(
            "runtime_check_skipped",
            extra={
                "check": "deployment_runtime",
                "substrate": result.substrate,
                "stage": result.failures[0].stage if result.failures else "unknown",
                "detail": result.failures[0].detail if result.failures else "",
            },
        )
    elif result.passed:
        log.info(
            "runtime_check_ok",
            extra={
                "check": "deployment_runtime",
                "substrate": result.substrate,
                "hash_1": result.hash_1,
                "hash_2": result.hash_2,
                "capture_duration_ms": result.capture_duration_ms,
            },
        )
    else:
        for failure in result.failures:
            log.warning(
                "runtime_check_failure",
                extra={
                    "check": "deployment_runtime",
                    "substrate": result.substrate,
                    "stage": failure.stage,
                    "detail": failure.detail,
                },
            )
    return result


def _run_plan_check(program: str, schemas: list[str], log: logging.Logger) -> PlanCheckResult:
    result = PlanCheck().run(program, schemas)
    if not result.deterministic:
        log.warning(
            "plan_drift",
            extra={
                "check": "plan_determinism",
                "hash_1": result.hash_1,
                "hash_2": result.hash_2,
                "diff": result.diff,
                "capture_duration_ms": result.capture_duration_ms,
            },
        )
    return result


def _run_deployment_arch_check(
    program: str, schemas: list[str], arch: str, log: logging.Logger
) -> DeploymentArchResult:
    result = DeploymentArchCheck(arch).run(program, schemas)
    if not result.images_checked:
        log.warning(
            "deployment_arch_no_images",
            extra={
                "check": "deployment_arch",
                "arch": arch,
                "unhandled_tokens": result.unhandled_tokens,
                "detail": (
                    "no extractable container images found in this program; arch check "
                    "skipped. Add an entry to IMAGE_EXTRACTORS for any of the unhandled "
                    "tokens to extend coverage."
                ),
            },
        )
    elif result.passed:
        log.info(
            "deployment_arch_ok",
            extra={
                "check": "deployment_arch",
                "arch": arch,
                "images_checked": result.images_checked,
                "capture_duration_ms": result.capture_duration_ms,
                "unhandled_tokens": result.unhandled_tokens,
            },
        )
    else:
        for failure in result.failures:
            log.warning(
                "deployment_arch_failure",
                extra={
                    "check": "deployment_arch",
                    "arch": arch,
                    "image": failure.image,
                    "reason": failure.reason,
                    "detail": failure.detail,
                },
            )
    return result


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.log_level)
    log = logging.getLogger("act")

    try:
        mg = MockGenerator(args.schema)
        oracle = CorrectnessOracle(args.schema)
        auto_load(oracle)
        _load_extra_rules(oracle, mg, args.rules)
        pipeline = ACTPipeline(mg, oracle)
        gate = CIGate(pipeline)
        exit_code = gate.evaluate(args.program)

        plan_result = _run_plan_check(args.program, args.schema, log)
        if not plan_result.deterministic:
            exit_code = max(exit_code, 1)

        arch_result = None
        if args.check_deployment_arch:
            arch_result = _run_deployment_arch_check(
                args.program, args.schema, args.check_deployment_arch, log
            )
            if not arch_result.passed:
                exit_code = max(exit_code, 1)

        runtime_result = None
        if args.check_deployment_runtime:
            runtime_result = _run_runtime_check(args.program, args.schema, log)
            skip_stages = {"substrate_unavailable", "spec_unsupported"}
            is_skip = any(f.stage in skip_stages for f in runtime_result.failures)
            if not runtime_result.passed and not is_skip:
                exit_code = max(exit_code, 1)

        if args.output:
            artefact = ReproducibilityArtefact(
                program_path=args.program,
                schemas=list(args.schema),
                plan_check=plan_result,
                deployment_arch=arch_result,
                runtime_check=runtime_result,
            )
            path = write_artefact(artefact, args.output)
            log.info("artefact_written", extra={"artefact_path": path})

        return exit_code
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 2
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
