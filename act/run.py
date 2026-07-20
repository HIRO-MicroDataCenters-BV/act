#!/usr/bin/env python3
"""
ACT - Automated Configuration Testing

Usage:
  uv run act --program <path> --schema <path> [<path> ...] [--output <dir>] [--rules checkov]
             [--check-deployment-arch <arch>]

Exit codes:
  0  all checks passed
  1  one or more violations found
  2  pipeline error
"""

import argparse
import importlib.metadata
import json
import logging
import pkgutil
import sys
import traceback
from pathlib import Path

from act import rules as _rules_pkg
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
from act.schema_resolver import SchemaResolveError, resolve_schemas


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


def _build_check_parser(cfg: ActConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="act check",
        description="Validate a Pulumi program against security rules without provisioning real infrastructure.",
    )
    parser.add_argument("--program", help="Path to Pulumi program file or project directory")
    parser.add_argument(
        "--schema",
        nargs="+",
        metavar="SCHEMA",
        help="Path(s) to provider schema JSON. Omit to auto-resolve from the program's imports. "
        "Repeat for multi-provider programs.",
    )
    parser.add_argument(
        "--schema-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="Extra directory to search for a local <plugin>.json when auto-resolving schemas. Repeatable.",
    )
    parser.add_argument(
        "--no-schema-fetch",
        action="store_true",
        help="Disable the network 'pulumi package get-schema' fallback (for offline or hardened runs); "
        "resolution then uses only local and cached schemas.",
    )
    parser.add_argument("--output", default=None, help="Directory to write run artefacts (optional)")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the one-line Summary footer (the PASS/FAIL report still prints).",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to an act.toml config file (default: ./act.toml if present). "
        "Precedence: CLI flags > env > file > default.",
    )
    parser.add_argument(
        "--log-level",
        default=cfg.log_level,
        choices=list(LOG_LEVELS),
        help="Log verbosity (default: WARNING - silent in CI unless set). Env: ACT_LOG_LEVEL.",
    )
    parser.add_argument(
        "--rules",
        nargs="*",
        default=list(cfg.rules),
        metavar="ENGINE",
        help="Extra rule engines to load (e.g. --rules checkov). Overrides ACT_RULES / config. Repeatable.",
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


# (arch, platform, spec_arch, image attr, command) for the base k3s substrates.
_BASE_ROWS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("amd64", "linux/amd64", "x86_64-linux", "k3s_image", _K3S_COMMAND),
    ("arm64", "linux/arm64", "aarch64-linux", "k3s_image", _K3S_COMMAND),
    ("riscv64", "linux/riscv64", "riscv64-linux", "k3s_riscv64_image", _K3S_RISCV64_COMMAND),
)


# Emulated non-native arches (riscv64 under QEMU) boot the cluster far slower than a native
# arch; scale their provision timeouts off the configurable base so a slow-but-working boot
# isn't a false timeout. Raising the base (ACT_K3S_STARTUP_TIMEOUT_S) scales these too.
_SLOW_ARCHS: frozenset[str] = frozenset({"riscv64"})
_SLOW_ARCH_TIMEOUT_SCALE = 4


def _default_substrates(cfg: ActConfig) -> list:
    """Substrate registry, restricted to cfg.runtime_archs.

    One base k3s row per arch, plus the amd64 GPU/FPGA/CXL accelerators, which
    declare their Extended Resource so feature-flagged specs schedule without real
    hardware. Images, timeouts, host port, resource names, and count come from ActConfig;
    slow emulated arches get scaled provision timeouts.
    """
    common: dict = {
        "extra_docker_args": _K3S_DOCKER_ARGS,
        "api_host_port": cfg.k3s_api_host_port,
    }
    substrates: list = []
    for arch, platform, spec_arch, image_attr, command in _BASE_ROWS:
        if arch not in cfg.runtime_archs:
            continue
        scale = _SLOW_ARCH_TIMEOUT_SCALE if arch in _SLOW_ARCHS else 1
        substrates.append(
            DockerSubstrate(
                image=getattr(cfg, image_attr),
                platform=platform,
                spec_arch=spec_arch,
                command=command,
                startup_timeout=cfg.k3s_startup_timeout_s * scale,
                api_ready_timeout=cfg.k8s_api_ready_timeout_s * scale,
                **common,
            )
        )

    if "amd64" in cfg.runtime_archs:
        accel: dict = dict(
            image=cfg.k3s_image,
            platform="linux/amd64",
            spec_arch="x86_64-linux",
            command=_K3S_COMMAND,
            count=cfg.accelerator_count,
            startup_timeout=cfg.k3s_startup_timeout_s,
            api_ready_timeout=cfg.k8s_api_ready_timeout_s,
            **common,
        )
        for substrate_cls, feature, resource_name in (
            (GpuSubstrate, "gpu", cfg.gpu_resource_name),
            (FpgaSubstrate, "fpga", cfg.fpga_resource_name),
            (CxlSubstrate, "cxl", cfg.cxl_resource_name),
        ):
            substrates.append(substrate_cls(features=frozenset({feature}), resource_name=resource_name, **accel))
    return substrates


# Runtime-check stages that mean "could not verify", not "failed": they never escalate the exit code.
_RUNTIME_SKIP_STAGES = frozenset({"substrate_unavailable", "spec_unsupported", "nothing_observed", "timeout"})


def _run_runtime_check(program: str, schemas: list[str], log: logging.Logger, cfg: ActConfig) -> RuntimeCheckResult:
    check = RuntimeCheck(
        substrates=_default_substrates(cfg),
        namespace=cfg.k8s_namespace,
        probe_timeout=cfg.k8s_probe_timeout_s,
    )
    result = check.run(program, schemas)

    skipped = any(f.stage in _RUNTIME_SKIP_STAGES for f in result.failures)

    if skipped:
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


def _run_plan_check(program: str, schemas: list[str], log: logging.Logger, cfg: ActConfig) -> PlanCheckResult:
    result = PlanCheck(capture_timeout_s=cfg.exec_timeout_s).run(program, schemas)
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


def _validate_inputs(program: str, schemas: list) -> str | None:
    """Return a one-line error if the program or any schema path is missing, else None."""
    if not Path(MockGenerator._entry_point(program)).is_file():
        return f"program not found: {program}"
    for schema in schemas:
        if not Path(schema).is_file():
            return f"schema not found: {schema}"
    return None


def _summary_line(pipeline_result, plan_result, arch_result, runtime_result) -> str:
    """One-line outcome summary for the check command (suppressed by --quiet)."""
    parts = []
    if pipeline_result is not None:
        n, v = pipeline_result.resource_count, len(pipeline_result.violations)
        parts.append(f"{n} resource{'' if n == 1 else 's'}")
        parts.append(f"{v} violation{'' if v == 1 else 's'}")
    parts.append("plan reproducible" if plan_result.deterministic else "plan drift")
    if arch_result is not None:
        parts.append(f"arch {'ok' if arch_result.passed else 'fail'}")
    if runtime_result is not None:
        skip = any(f.stage in _RUNTIME_SKIP_STAGES for f in runtime_result.failures)
        parts.append("runtime " + ("skipped" if skip else "ok" if runtime_result.passed else "fail"))
    return "Summary: " + ", ".join(parts)


def _resolve_config_path(argv) -> str | None:
    """Pre-parse --config so cfg (hence the parser defaults) reflects the file."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=None)
    known, _ = pre.parse_known_args(argv)
    if known.config:
        return known.config
    return "act.toml" if Path("act.toml").is_file() else None


def _cmd_check(argv=None) -> int:
    cfg = ActConfig.load(config_path=_resolve_config_path(argv))
    parser = _build_check_parser(cfg)
    args = parser.parse_args(argv)

    if not args.program:
        parser.error("the following arguments are required: --program")

    _configure_logging(args.log_level)
    log = logging.getLogger("act")

    # The program must exist before we can scan it for providers.
    error = _validate_inputs(args.program, [])
    if error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2

    # --schema is a full override; otherwise resolve schemas from the program's imports.
    allow_fetch = cfg.schema_fetch == "allow" and not args.no_schema_fetch
    schema_dirs = args.schema_dir or list(cfg.schema_dirs)  # CLI overrides env/config
    try:
        schemas = resolve_schemas(args.program, args.schema, schema_dirs=schema_dirs, allow_fetch=allow_fetch)
    except SchemaResolveError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    error = _validate_inputs(args.program, schemas)
    if error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2

    try:
        mg = MockGenerator(schemas, exec_timeout_s=cfg.exec_timeout_s)
        oracle = CorrectnessOracle(schemas)
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
        # A pipeline error (exit 2) means the plan-capture subprocess would fail too;
        # stop here instead of surfacing a second traceback from the reproducibility checks.
        if exit_code == 2:
            return exit_code

        plan_result = _run_plan_check(args.program, schemas, log, cfg)
        if not plan_result.deterministic:
            exit_code = max(exit_code, 1)

        arch_result = None
        if args.check_deployment_arch:
            arch_result = _run_deployment_arch_check(args.program, schemas, args.check_deployment_arch, log, cfg)
            if not arch_result.passed:
                exit_code = max(exit_code, 1)
            if any(f.reason == "docker_missing" for f in arch_result.failures):
                print("[HINT] deployment-arch check needs docker; run 'act doctor'.", file=sys.stderr)

        runtime_result = None
        if args.check_deployment_runtime:
            runtime_result = _run_runtime_check(args.program, schemas, log, cfg)
            is_skip = any(f.stage in _RUNTIME_SKIP_STAGES for f in runtime_result.failures)
            if not runtime_result.passed and not is_skip:
                exit_code = max(exit_code, 1)
            if any(f.stage == "substrate_unavailable" for f in runtime_result.failures):
                print(
                    "[HINT] deployment-runtime check skipped; run 'act doctor' to check prerequisites.",
                    file=sys.stderr,
                )

        if args.output:
            artefact = ReproducibilityArtefact(
                program_path=args.program,
                schemas=list(schemas),
                plan_check=plan_result,
                deployment_arch=arch_result,
                runtime_check=runtime_result,
            )
            path = write_artefact(artefact, args.output)
            log.info("artefact_written", extra={"artefact_path": path})

        if not args.quiet:
            print(_summary_line(gate.last_result, plan_result, arch_result, runtime_result))

        return exit_code
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 2


def _version_string() -> str:
    """ACT version from the VERSION file, falling back to installed package metadata."""
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        text = version_file.read_text().strip()
        if text:
            return text
    except OSError:
        pass
    try:
        return importlib.metadata.version("act")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _print_top_level_help() -> None:
    print(
        "usage: act <command> [options]\n"
        "\n"
        "Validate Pulumi programs against security rules without provisioning real infrastructure.\n"
        "\n"
        "commands:\n"
        "  check            Validate a program (default when no command is given)\n"
        "  doctor           Report external-tool availability and per-flag prerequisites\n"
        "  list-rules       List the security rules ACT will apply\n"
        "  list-providers   List providers ACT has built-in rules for\n"
        "  version          Print the ACT version\n"
        "\n"
        "Run 'act check --help' for the full list of check options.\n"
        "Bare 'act --program <p> --schema <s>' runs 'check' directly."
    )


def _cmd_list_rules(argv=None) -> int:
    # Empty schema list constructs the oracle without file I/O; rules need no schema.
    oracle = CorrectnessOracle([])
    auto_load(oracle)
    rules = oracle.registered_rules()
    if not rules:
        print("No built-in rules registered.")
        return 0
    print("Security rules ACT applies to every program:")
    for scoped_type, fn in rules:
        scope = scoped_type or "any resource type"
        summary = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
        print(f"  {fn.__name__}  [{scope}]" + (f"  {summary}" if summary else ""))
    print("\nAdd provider-level checks with: act check --rules checkov")
    return 0


def _cmd_list_providers(argv=None) -> int:
    providers = sorted(m.name for m in pkgutil.iter_modules(_rules_pkg.__path__))
    print("Providers with built-in ACT rules:")
    for name in providers or ["(none)"]:
        print(f"  {name}")
    print("\nThe checkov engine (act check --rules checkov) adds coverage for many more providers.")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Bare `act` or a top-level help flag prints the command overview.
    if not argv or argv[0] in ("-h", "--help"):
        _print_top_level_help()
        return 0
    if argv[0] in ("version", "--version", "-V"):
        print(_version_string())
        return 0
    if argv[0] == "doctor":
        from act.doctor import run as _doctor_run

        return _doctor_run()
    if argv[0] == "list-rules":
        return _cmd_list_rules(argv[1:])
    if argv[0] == "list-providers":
        return _cmd_list_providers(argv[1:])
    if argv[0] == "check":
        return _cmd_check(argv[1:])
    # No recognised command: default to `check` so `act --program … --schema …` still works.
    return _cmd_check(argv)


if __name__ == "__main__":
    sys.exit(main())
