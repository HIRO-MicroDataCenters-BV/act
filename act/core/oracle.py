from typing import Callable, List, Optional, Tuple

import json
import logging

from act.core.violations import Violation
from act.plugins.base import OraclePlugin

log = logging.getLogger(__name__)


class CorrectnessOracle(OraclePlugin):
    """Provider-agnostic rule engine.

    Combines two violation sources:
    - Schema inference: required-field presence and top-level type checks
    - Plugged rules: plain functions (inputs: dict) -> List[Violation]

    The oracle has zero provider-specific knowledge. All domain rules are
    injected via add_rule().
    """

    def __init__(self, schema_path: str | list[str]):
        paths = [schema_path] if isinstance(schema_path, str) else schema_path
        merged_resources: dict = {}
        for p in paths:
            with open(p) as f:
                merged_resources.update(json.load(f).get("resources", {}))
        self._schema = {"resources": merged_resources}
        self._rules: List[Tuple[Optional[str], Callable[[dict], List[Violation]]]] = []

    def add_rule(
        self,
        rule_fn: Callable[[dict], List[Violation]],
        resource_type: Optional[str] = None,
    ) -> None:
        """Register a rule function.

        resource_type: Pulumi token to scope the rule (e.g. "cape:compute:Instance").
                       If None, the rule runs for every resource type.
        """
        self._rules.append((resource_type, rule_fn))

    def check(self, resource_type: str, inputs: dict) -> List[Violation]:
        """Return all violations for a single resource.

        resource_type: full Pulumi token, e.g. "cape:compute:Instance"
        inputs: the resource output dict from MockGenerator.run_with_mocks()
        """
        violations = self._infer_from_schema(resource_type, inputs)
        for scoped_type, rule in self._rules:
            if scoped_type is None or scoped_type == resource_type:
                violations.extend(rule(inputs))
        if violations:
            log.debug(
                "oracle.violations",
                extra={
                    "resource_type": resource_type,
                    "count": len(violations),
                    "fields": [v.field for v in violations],
                },
            )
        return violations

    def _infer_from_schema(self, resource_type: str, inputs: dict) -> List[Violation]:
        """Auto-check required fields and scalar types defined in the schema.

        Covers only what the schema explicitly declares. When schemas gain
        typed sub-properties, this method picks them up automatically.
        """
        resource_schema = self._schema.get("resources", {}).get(resource_type, {})
        if not resource_schema:
            return []

        required = resource_schema.get("requiredInputs", [])
        input_props = resource_schema.get("inputProperties", {})
        violations = []

        for field in required:
            if inputs.get(field) is None:
                violations.append(
                    Violation(
                        field=field,
                        message=f"Required field '{field}' is missing",
                        severity="HIGH",
                    )
                )

        type_checks = {
            "string": str,
            "integer": int,
            "boolean": bool,
        }
        for field, prop_schema in input_props.items():
            if field not in inputs:
                continue
            value = inputs[field]
            expected_type = prop_schema.get("type")
            python_type = type_checks.get(expected_type)
            if python_type and not isinstance(value, python_type):
                violations.append(
                    Violation(
                        field=field,
                        message=f"Field '{field}' must be {expected_type}",
                        severity="MEDIUM",
                    )
                )
                continue  # skip range/enum checks if type is already wrong

            minimum = prop_schema.get("minimum")
            maximum = prop_schema.get("maximum")
            if minimum is not None and isinstance(value, (int, float)) and value < minimum:
                violations.append(
                    Violation(
                        field=field,
                        message=f"Field '{field}' must be >= {minimum}, got {value}",
                        severity="HIGH",
                    )
                )
            if maximum is not None and isinstance(value, (int, float)) and value > maximum:
                violations.append(
                    Violation(
                        field=field,
                        message=f"Field '{field}' must be <= {maximum}, got {value}",
                        severity="HIGH",
                    )
                )

            allowed = prop_schema.get("enum")
            if allowed is not None and value not in allowed:
                violations.append(
                    Violation(
                        field=field,
                        message=f"Field '{field}' must be one of {allowed}, got {value!r}",
                        severity="HIGH",
                    )
                )

        return violations
