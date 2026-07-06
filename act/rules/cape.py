"""CAPE security rules: (inputs: dict) -> List[Violation].

Pulumi serializes InstanceSpecArgs fields to camelCase under the "spec" key
(e.g. securityGroupRef, sshKeys). Only add rules for fields that exist in cape-sdks.
"""

from typing import List

from act.core.violations import Violation


def rule_no_exposed_instance(inputs: dict) -> List[Violation]:
    """securityGroupRef must be present in Instance spec."""
    spec = inputs.get("spec") or {}
    if not spec.get("securityGroupRef"):
        return [
            Violation(
                field="spec.securityGroupRef",
                message="Instance has no security group — network traffic is uncontrolled",
                severity="HIGH",
            )
        ]
    return []


def rule_no_unprotected_ssh(inputs: dict) -> List[Violation]:
    """sshKeys without securityGroupRef exposes SSH to any source."""
    spec = inputs.get("spec") or {}
    if spec.get("sshKeys") and not spec.get("securityGroupRef"):
        return [
            Violation(
                field="spec.sshKeys",
                message="SSH keys configured but no security group — SSH access is open",
                severity="HIGH",
            )
        ]
    return []


def register(oracle) -> None:
    oracle.add_rule(rule_no_exposed_instance, resource_type="cape:compute:Instance")
    oracle.add_rule(rule_no_unprotected_ssh, resource_type="cape:compute:Instance")
