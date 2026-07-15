"""Run MockGenerator on a program and emit canonical JSON to stdout.

Both invocations of the plan-determinism check call this; the caller hashes
the stdout.
"""

import argparse
import json
import sys

from act.core.mock_generator import MockGenerator


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="act-capture",
        description="Run a Pulumi program through MockGenerator and emit canonical JSON to stdout.",
    )
    parser.add_argument("--program", required=True, help="Path to Pulumi program file or project directory")
    parser.add_argument(
        "--schema",
        required=True,
        nargs="+",
        metavar="SCHEMA",
        help="Path(s) to provider schema JSON. Repeat for multi-provider programs.",
    )
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    mg = MockGenerator(args.schema)
    result = mg.run_with_mocks(args.program)
    sys.stdout.write(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
