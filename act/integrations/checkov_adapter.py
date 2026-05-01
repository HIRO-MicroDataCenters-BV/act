"""Checkov integration — wraps Checkov checks as ACT rules.

Fully dynamic: the Checkov check type is derived from the Pulumi token prefix.
No provider names are hardcoded.

The first segment of the Pulumi token maps to the Checkov check type:
  'kubernetes:apps/v1:Deployment' → checkov.kubernetes runner
  'terraform:aws/s3:Bucket'       → checkov.terraform runner

Rules are registered unscoped (resource_type=None) so they apply across
token variants (schema versions vs runtime versions). The rule self-filters:
it only runs when mock outputs contain enough structure for Checkov to evaluate.

Usage:
    from act.integrations.checkov_adapter import load_checkov_rules

    # Scope to a specific resource type
    load_checkov_rules(oracle, resource_type="kubernetes:apps/v1:Deployment")

    # Apply to all resources from a provider (recommended for CLI use)
    load_checkov_rules(oracle, check_type="kubernetes")
"""

from typing import List

import importlib
import logging
import os
import pkgutil
import tempfile

import yaml

from act.core.oracle import Violation

logging.getLogger("checkov").setLevel(logging.ERROR)

_checks_loaded: set[str] = set()


def _load_checks(check_type: str) -> None:
    """Dynamically import all Checkov check modules for the given check type."""
    if check_type in _checks_loaded:
        return
    try:
        pkg = importlib.import_module(f"checkov.{check_type}.checks.resource")
        for _, name, _ in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            try:
                importlib.import_module(name)
            except Exception:
                pass
        _checks_loaded.add(check_type)
    except ModuleNotFoundError:
        raise ValueError(f"No Checkov checks found for provider: {check_type!r}")


def _severity(record) -> str:
    if record.severity and record.severity.name not in ("NONE", "UNKNOWN"):
        return record.severity.name
    return "MEDIUM"


def _run_checkov(check_type: str, outputs: dict) -> List[Violation]:
    """Run the Checkov runner for check_type against the mock outputs dict."""
    try:
        runner_mod = importlib.import_module(f"checkov.{check_type}.runner")
    except ModuleNotFoundError:
        raise ValueError(f"No Checkov runner found for provider: {check_type!r}")

    RunnerFilter = importlib.import_module("checkov.runner_filter").RunnerFilter

    # Checkov requires metadata to build its internal context — add a fallback if absent.
    payload = outputs if "metadata" in outputs else {**outputs, "metadata": {"name": "act-resource"}}

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(payload, f)
        tmp_path = f.name

    try:
        report = runner_mod.Runner().run(
            root_folder=None,
            files=[tmp_path],
            runner_filter=RunnerFilter(show_progress_bar=False),
        )
        return [
            Violation(field=fc.check_id, message=fc.check_name, severity=_severity(fc)) for fc in report.failed_checks
        ]
    finally:
        os.unlink(tmp_path)


def load_checkov_rules(
    oracle,
    check_type: str = None,
    resource_type: str = None,
) -> None:
    """Register Checkov checks on the oracle.

    check_type: Checkov provider name, e.g. 'kubernetes'. Derived from
                resource_type if not provided.
    resource_type: Pulumi token to scope the rule (e.g. 'kubernetes:apps/v1:Deployment').
                   If None, the rule applies to all resources and self-filters by
                   inspecting mock outputs — recommended when the schema token may
                   differ from the runtime token.
    """
    if not check_type and not resource_type:
        raise ValueError("Provide at least one of check_type or resource_type.")

    resolved_type = check_type or resource_type.split(":")[0]
    _load_checks(resolved_type)

    def _rule(inputs: dict) -> List[Violation]:
        return _run_checkov(resolved_type, inputs)

    oracle.add_rule(_rule, resource_type=resource_type)
