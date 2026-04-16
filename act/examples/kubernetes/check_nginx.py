"""
Run nginx_deployment.py under ACT mocks and check for security violations.

Usage (from src/):
    uv run python act/examples/kubernetes/check_nginx.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from act.core.mock_generator import MockGenerator

SCHEMA = Path(__file__).parent / "schema.json"
PROGRAM = Path(__file__).parent / "nginx_deployment.py"


def check_run_as_non_root(result: dict) -> list[str]:
    violations = []
    for resource_name, outputs in result.items():
        containers = (
            outputs.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for c in containers:
            if not c.get("securityContext", {}).get("runAsNonRoot"):
                violations.append(
                    f"[{resource_name}] container '{c['name']}': runAsNonRoot not set"
                )
    return violations


def main():
    mg = MockGenerator(str(SCHEMA))
    result = mg.run_with_mocks(str(PROGRAM))

    print("=== Mock outputs ===")
    print(json.dumps(result, indent=2, default=str))
    print()

    violations = check_run_as_non_root(result)
    if violations:
        print("=== Violations ===")
        for v in violations:
            print("FAIL:", v)
        sys.exit(1)
    else:
        print("OK: no violations found")
        sys.exit(0)


if __name__ == "__main__":
    main()
