from typing import Callable, List, Optional, Tuple

import logging

from act.core.schema import load_merged_resources
from act.core.violations import Violation, count_by_source
from act.plugins.base import OraclePlugin

log = logging.getLogger(__name__)


def _is_empty_required(value) -> bool:
    """True for a missing or empty required value (0, False, and {} stay valid)."""
    return value is None or (isinstance(value, (str, list)) and len(value) == 0)


def _type_matches(value, expected_type: str) -> bool:
    """Whether value matches a scalar schema type; bool is not an integer."""
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "string":
        return isinstance(value, str)
    return True  # unknown/complex type: not checked here


class CorrectnessOracle(OraclePlugin):
    """Provider-agnostic rule engine.

    Two violation sources: schema inference (required fields, top-level types)
    and plugged rules injected via add_rule(). No provider-specific knowledge.
    """

    def __init__(self, schema_path: str | list[str]):
        self._schema = load_merged_resources(schema_path)
        self._rules: List[Tuple[Optional[str], Callable[[dict], List[Violation]]]] = []
        self._sources: dict[int, str] = {}

    def add_rule(
        self,
        rule_fn: Callable[[dict], List[Violation]],
        resource_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        """Register a rule function.

        resource_type: Pulumi token to scope the rule; None runs it for every type.
        source: name this rule set reports itself as, so a report can say which engine
                raised a violation. Stamped onto the rule's violations by check().
        """
        self._rules.append((resource_type, rule_fn))
        if source:
            self._sources[id(rule_fn)] = source

    def registered_rules(self) -> List[Tuple[Optional[str], Callable[[dict], List[Violation]]]]:
        """Return the (resource_type, rule_fn) pairs registered via add_rule()."""
        return list(self._rules)

    def check(self, resource_type: str, inputs: dict) -> List[Violation]:
        """Return all violations for a single resource.

        inputs: the resource output dict from MockGenerator.run_with_mocks().
        """
        violations = self._infer_from_schema(resource_type, inputs)
        for v in violations:
            v.source = v.source or "schema"
        for scoped_type, rule in self._rules:
            if scoped_type is None or scoped_type == resource_type:
                found = rule(inputs)
                source = self._sources.get(id(rule), "")
                for v in found:
                    v.source = v.source or source
                violations.extend(found)
        if violations:
            log.debug(
                "oracle.violations",
                extra={
                    "resource_type": resource_type,
                    "count": len(violations),
                    "fields": [v.field for v in violations],
                    "by_source": count_by_source(violations),
                },
            )
        return violations

    def _infer_from_schema(self, resource_type: str, inputs: dict) -> List[Violation]:
        """Check required fields and scalar types declared in the schema (auto-extends as the schema grows)."""
        resource_schema = self._schema.get("resources", {}).get(resource_type, {})
        if not resource_schema:
            return []

        required = resource_schema.get("requiredInputs", [])
        input_props = resource_schema.get("inputProperties", {})
        violations = []

        for field in required:
            if _is_empty_required(inputs.get(field)):
                violations.append(
                    Violation(
                        field=field,
                        message=f"Required field '{field}' is missing or empty",
                        severity="HIGH",
                    )
                )

        for field, prop_schema in input_props.items():
            value = inputs.get(field)
            # Skip None; the required-field check above already covers it (avoids double violation).
            if value is None:
                continue
            expected_type = prop_schema.get("type")
            if expected_type in ("string", "integer", "boolean") and not _type_matches(value, expected_type):
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
