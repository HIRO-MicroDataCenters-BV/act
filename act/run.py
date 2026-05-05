#!/usr/bin/env python3
"""
ACT — Automated Configuration Testing

Usage:
  python act/run.py --program <path> --schema <path> [<path> ...] [--output <dir>] [--rules checkov]

Exit codes:
  0  all checks passed
  1  one or more violations found
  2  pipeline error
"""

import argparse
import sys

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.core.pipeline import ACTPipeline
from act.gate.ci_gate import CIGate
from act.integrations.checkov_adapter import load_checkov_rules
from act.rules import auto_load


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
        "--rules",
        nargs="*",
        default=[],
        metavar="ENGINE",
        help="Extra rule engines to load (e.g. --rules checkov). Repeatable.",
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        mg = MockGenerator(args.schema)
        oracle = CorrectnessOracle(args.schema)
        auto_load(oracle)
        _load_extra_rules(oracle, mg, args.rules)
        pipeline = ACTPipeline(mg, oracle)
        gate = CIGate(pipeline)
        return gate.evaluate(args.program)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 2
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
