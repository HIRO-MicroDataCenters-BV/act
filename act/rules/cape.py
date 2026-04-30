"""CAPE security rules.

Each rule is a plain function: (inputs: dict) -> List[Violation]

inputs is the resource output dict from MockGenerator.run_with_mocks() for a
single resource. Pulumi serializes InstanceSpecArgs fields to camelCase inside
the "spec" key (e.g. securityGroupRef, sshKeys).

Only add rules for fields that exist in the real cape-sdks.
"""

from typing import List

from act.core.oracle import Violation


def rule_no_exposed_instance(inputs: dict) -> List[Violation]:
    """securityGroupRef must be present in Instance spec."""
    spec = inputs.get("spec", {})
    if not spec.get("securityGroupRef"):
        return [Violation(
            field="spec.securityGroupRef",
            message="Instance has no security group — network traffic is uncontrolled",
            severity="HIGH",
        )]
    return []


def rule_no_unprotected_ssh(inputs: dict) -> List[Violation]:
    """sshKeys without securityGroupRef exposes SSH to any source."""
    spec = inputs.get("spec", {})
    if spec.get("sshKeys") and not spec.get("securityGroupRef"):
        return [Violation(
            field="spec.sshKeys",
            message="SSH keys configured but no security group — SSH access is open",
            severity="HIGH",
        )]
    return []
