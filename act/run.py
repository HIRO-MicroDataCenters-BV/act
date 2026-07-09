#!/usr/bin/env python3
"""
ACT - Automated Configuration Testing

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
import sys
import traceback

from act.acv.agent import ACTCognitiveValidator
from act.config import ACV_MODES, LOG_LEVELS, ActConfig
from act.core.fuzz_runner import FuzzRunner
from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.core.pipeline import ACTPipeline
from act.core.property_runner import PropertyRunner
from act.gate.ci_gate import CIGate
from act.integrations.checkov_adapter import load_checkov_rules
from act.reproducibility import (
    CxlSubstrate,
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
        "verdict",
        "risk_level",
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
    logging.getLogger("act").setLevel(level)


_KNOWN_RULE_ENGINES = frozenset({"checkov"})


def _load_extra_rules(oracle, mg, engines: list) -> None:
    """Load additional rule engines requested via --rules."""
    log = logging.getLogger("act")
    for engine in engines:
        if engine not in _KNOWN_RULE_ENGINES:
            log.warning(
                "rules.unknown_engine",
                extra={"reason": f"unknown rule engine '{engine}' ignored; known: {sorted(_KNOWN_RULE_ENGINES)}"},
            )
    if "checkov" not in engines:
        return
    # One unscoped rule per provider; avoids schema vs runtime token mismatches.
    providers = {info["token"].split(":")[0] for info in mg._type_map.values()}
    for provider in providers:
        try:
            load_checkov_rules(oracle, check_type=provider)
        except ValueError as exc:
            # No Checkov coverage for this provider; log so a broken install leaves a breadcrumb.
            log.debug(
                "checkov.skipped_provider",
                extra={"provider": provider, "reason": str(exc)},
            )


def build_arg_parser(cfg: ActConfig) -> argparse.ArgumentParser:
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
        default=cfg.log_level,
        choices=list(LOG_LEVELS),
        help="Log verbosity (default: WARNING - silent in CI unless set). Env: ACT_LOG_LEVEL.",
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
        "against it twice, hash the probed outputs, and compare. Requires docker, "
        "kubectl, and the pulumi CLI.",
    )
    parser.add_argument(
        "--acv-mode",
        choices=list(ACV_MODES),
        default=cfg.acv_mode,
        help="Whether ACV findings gate the exit code. advisory (default) never blocks; "
        "blocking fails the gate on an ACV FAIL. Env: ACT_ACV_MODE.",
    )
    return parser


_K3S_DOCKER_ARGS: tuple[str, ...] = ("--privileged", "--tmpfs", "/run", "--tmpfs", "/var/run")
_K3S_COMMAND: tuple[str, ...] = (
    "server",
    "--disable=traefik",
    "--write-kubeconfig-mode=644",
    # native snapshotter avoids overlayfs mounts that fail under QEMU binfmt.
    "--snapshotter=native",
)

# riscv64 QEMU binfmt can't run iptables components; the image bundles CNI +
# a bridge conflist so kubelet reaches NetworkReady without kube-proxy/flannel.
_K3S_RISCV64_COMMAND: tuple[str, ...] = _K3S_COMMAND + (
    "--disable-kube-proxy",
    "--flannel-backend=none",
    "--disable-network-policy",
)


def _default_substrates(cfg: ActConfig) -> list:
    """Substrate registry, restricted to cfg.runtime_archs.

    Base rows (one per arch) plus the amd64 GPU/FPGA/CXL accelerators, which
    declare their Extended Resource so feature-flagged specs schedule without
    real hardware. Images, timeouts, host port, resource names, and count come
    from ActConfig.
    """
    base = {
        "amd64": lambda: DockerSubstrate(
            image=cfg.k3s_image, platform="linux/amd64", spec_arch="x86_64-linux", command=_K3S_COMMAND, **_common(cfg)
        ),
        "arm64": lambda: DockerSubstrate(
            image=cfg.k3s_image, platform="linux/arm64", spec_arch="aarch64-linux", command=_K3S_COMMAND, **_common(cfg)
        ),
        "riscv64": lambda: DockerSubstrate(
            image=cfg.k3s_riscv64_image,
            platform="linux/riscv64",
            spec_arch="riscv64-linux",
            command=_K3S_RISCV64_COMMAND,
            **_common(cfg),
        ),
    }
    substrates = [factory() for arch, factory in base.items() if arch in cfg.runtime_archs]

    if "amd64" in cfg.runtime_archs:
        accel = dict(
            image=cfg.k3s_image,
            platform="linux/amd64",
            spec_arch="x86_64-linux",
            command=_K3S_COMMAND,
            count=cfg.accelerator_count,
            api_ready_timeout=cfg.k8s_api_ready_timeout_s,
            **_common(cfg),
        )
        substrates += [
            GpuSubstrate(features=frozenset({"gpu"}), resource_name=cfg.gpu_resource_name, **accel),
            FpgaSubstrate(features=frozenset({"fpga"}), resource_name=cfg.fpga_resource_name, **accel),
            CxlSubstrate(features=frozenset({"cxl"}), resource_name=cfg.cxl_resource_name, **accel),
        ]
    return substrates


def _common(cfg: ActConfig) -> dict:
    """Substrate fields shared by every row."""
    return {
        "extra_docker_args": _K3S_DOCKER_ARGS,
        "api_host_port": cfg.k3s_api_host_port,
        "startup_timeout": cfg.k3s_startup_timeout_s,
    }


def _run_runtime_check(program: str, schemas: list[str], log: logging.Logger, cfg: ActConfig) -> RuntimeCheckResult:
    check = RuntimeCheck(
        substrates=_default_substrates(cfg),
        namespace=cfg.k8s_namespace,
        probe_timeout=cfg.k8s_probe_timeout_s,
    )
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
    program: str, schemas: list[str], arch: str, log: logging.Logger, cfg: ActConfig
) -> DeploymentArchResult:
    result = DeploymentArchCheck(arch, timeout=cfg.image_boot_timeout_s).run(program, schemas)
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
    cfg = ActConfig.from_env()
    parser = build_arg_parser(cfg)
    args = parser.parse_args(argv)

    _configure_logging(args.log_level)
    log = logging.getLogger("act")

    try:
        mg = MockGenerator(args.schema)
        oracle = CorrectnessOracle(args.schema)
        auto_load(oracle)
        _load_extra_rules(oracle, mg, args.rules)
        # ACV is additive; from_env returns None unless ACT_ACV_MODEL + a base URL
        # are set (and the optional acv extra is installed).
        acv = ACTCognitiveValidator.from_env(cfg)
        # Fuzz + property runners only fire on Path B (parameterized programs); the
        # pipeline skips them for Path A. Depth is tunable via ACT_FUZZ_ITERATIONS /
        # ACT_PROPERTY_MAX_EXAMPLES.
        fuzz_runner = FuzzRunner(mg, oracle, iterations=cfg.fuzz_iterations)
        property_runner = PropertyRunner(mg, oracle, max_examples=cfg.property_max_examples)
        pipeline = ACTPipeline(
            mg,
            oracle,
            fuzz_runner=fuzz_runner,
            property_runner=property_runner,
            acv=acv,
            acv_blocking=(args.acv_mode == "blocking"),
        )
        gate = CIGate(pipeline)
        exit_code = gate.evaluate(args.program)

        plan_result = _run_plan_check(args.program, args.schema, log)
        if not plan_result.deterministic:
            exit_code = max(exit_code, 1)

        arch_result = None
        if args.check_deployment_arch:
            arch_result = _run_deployment_arch_check(args.program, args.schema, args.check_deployment_arch, log, cfg)
            if not arch_result.passed:
                exit_code = max(exit_code, 1)

        runtime_result = None
        if args.check_deployment_runtime:
            runtime_result = _run_runtime_check(args.program, args.schema, log, cfg)
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
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
