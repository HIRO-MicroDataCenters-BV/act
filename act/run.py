#!/usr/bin/env python3
"""
ACT — Automated Configuration Testing

Usage:
  python act/run.py --program <path> --schema <path> [--output <dir>]

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
from act.rules import auto_load


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="act",
        description="Validate a Pulumi program against security rules without provisioning real infrastructure.",
    )
    parser.add_argument("--program", required=True, help="Path to Pulumi program file or project directory")
    parser.add_argument("--schema", required=True, help="Path to provider schema JSON")
    parser.add_argument("--output", default=None, help="Directory to write run artefacts (optional)")
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        mg = MockGenerator(args.schema)
        oracle = CorrectnessOracle(args.schema)
        auto_load(oracle)
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
