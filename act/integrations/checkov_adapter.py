"""Checkov integration: wraps Checkov checks as ACT rules.

The Checkov check type is derived from the Pulumi token's first segment
('kubernetes:apps/v1:Deployment' -> checkov.kubernetes runner). Rules register
unscoped so they apply across token variants (schema vs runtime versions), and
self-filter to runs where mock outputs have enough structure to evaluate.

Usage:
    load_checkov_rules(oracle, resource_type="kubernetes:apps/v1:Deployment")
    load_checkov_rules(oracle, check_type="kubernetes")  # all resources; recommended for CLI
"""

from typing import List, Optional

import importlib
import logging
import os
import pkgutil
import tempfile

import yaml  # type: ignore[import-untyped]

from act.core.violations import Violation

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

    # Checkov needs metadata to build its context; add a fallback if absent.
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
    check_type: Optional[str] = None,
    resource_type: Optional[str] = None,
) -> None:
    """Register Checkov checks on the oracle.

    check_type: Checkov provider name (e.g. 'kubernetes'); derived from resource_type if absent.
    resource_type: Pulumi token to scope the rule; None applies to all resources and
                   self-filters, recommended when schema and runtime tokens may differ.
    """
    if not check_type and not resource_type:
        raise ValueError("Provide at least one of check_type or resource_type.")

    resolved_type = check_type or (resource_type or "").split(":")[0]
    _load_checks(resolved_type)

    def _rule(inputs: dict) -> List[Violation]:
        return _run_checkov(resolved_type, inputs)

    oracle.add_rule(_rule, resource_type=resource_type)
