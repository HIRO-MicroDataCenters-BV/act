"""CAPE rules: one clean and one violating input per rule, plus the end-to-end fixtures.

Inputs mirror what MockGenerator returns: camelCase keys under "spec", numbers as floats.
"""

import pytest

from act.core.mock_generator import MockGenerator
from act.core.oracle import CorrectnessOracle
from act.rules import cape
from act.rules.cape import (
    rule_ingress_rule_has_protocol,
    rule_ingress_rule_has_source,
    rule_inline_security_group_rules,
    rule_load_balancer_security_group,
    rule_nat_gateway_security_group,
    rule_no_exposed_instance,
    rule_no_unprotected_ssh,
    rule_no_wildcard_role_assignment,
    rule_no_wildcard_role_resources,
    rule_node_pool_security_group,
    rule_node_root_volume_size,
    rule_public_nic_security_group,
    rule_restricted_kubernetes_api,
    rule_scoped_role_assignment,
    rule_valid_image_architecture,
    rule_valid_security_group_rule,
)

SG = {"resource": "security-groups/default"}
SOURCE = [{"resource": "security-groups/frontend"}]


def _fields(violations):
    return [v.field for v in violations]


# ---------------------------------------------------------------------------
# Instance
# ---------------------------------------------------------------------------


def test_instance_with_security_group_passes():
    inputs = {"spec": {"securityGroupRef": SG, "sshKeys": ["ssh-keys/k"]}}
    assert rule_no_exposed_instance(inputs) == []
    assert rule_no_unprotected_ssh(inputs) == []


def test_instance_without_security_group_flagged():
    violations = rule_no_exposed_instance({"spec": {}})
    assert _fields(violations) == ["spec.securityGroupRef"]
    assert violations[0].severity == "HIGH"


def test_instance_ssh_without_security_group_flagged_once():
    inputs = {"spec": {"sshKeys": ["ssh-keys/k"]}}
    both = rule_no_exposed_instance(inputs) + rule_no_unprotected_ssh(inputs)
    assert _fields(both) == ["spec.sshKeys"]


# ---------------------------------------------------------------------------
# KubernetesCluster
# ---------------------------------------------------------------------------


def test_restricted_kubernetes_api_passes():
    assert rule_restricted_kubernetes_api({"spec": {"restrictKubernetesApi": ["10.0.0.0/8"]}}) == []


@pytest.mark.parametrize("allowed", [None, [], ["0.0.0.0/0"], ["10.0.0.0/8", "::/0"]])
def test_unrestricted_kubernetes_api_flagged(allowed):
    spec = {} if allowed is None else {"restrictKubernetesApi": allowed}
    violations = rule_restricted_kubernetes_api({"spec": spec})
    assert _fields(violations) == ["spec.restrictKubernetesApi"]
    assert violations[0].severity == "HIGH"


# ---------------------------------------------------------------------------
# SecurityGroupRule (standalone) and SecurityGroup (inline rules)
# ---------------------------------------------------------------------------

GOOD_RULE = {"direction": "ingress", "protocol": "tcp", "ports": {"from": 443.0}, "sourceRef": SOURCE}


def test_scoped_ingress_rule_passes():
    inputs = {"spec": GOOD_RULE}
    assert rule_ingress_rule_has_source(inputs) == []
    assert rule_ingress_rule_has_protocol(inputs) == []
    assert rule_valid_security_group_rule(inputs) == []


def test_ingress_rule_without_source_flagged():
    violations = rule_ingress_rule_has_source({"spec": {"direction": "ingress", "protocol": "tcp"}})
    assert _fields(violations) == ["spec.sourceRef"]
    assert violations[0].severity == "HIGH"


def test_ingress_rule_without_protocol_flagged():
    violations = rule_ingress_rule_has_protocol({"spec": {"direction": "ingress", "sourceRef": SOURCE}})
    assert _fields(violations) == ["spec.protocol"]
    assert violations[0].severity == "MEDIUM"


def test_egress_rule_without_source_or_protocol_passes():
    inputs = {"spec": {"direction": "egress"}}
    assert rule_ingress_rule_has_source(inputs) == []
    assert rule_ingress_rule_has_protocol(inputs) == []


@pytest.mark.parametrize(
    "spec, field",
    [
        ({}, "spec.direction"),
        ({"direction": "inbound"}, "spec.direction"),
        ({"direction": "ingress", "protocol": "https"}, "spec.protocol"),
        ({"direction": "ingress", "version": "IPv5"}, "spec.version"),
        ({"direction": "ingress", "ports": {"from": 0.0}}, "spec.ports.from"),
        ({"direction": "ingress", "ports": {"to": 70000.0}}, "spec.ports.to"),
        ({"direction": "ingress", "ports": {"from": 22.5}}, "spec.ports.from"),
        ({"direction": "ingress", "ports": {"list": [80.0, 65536.0]}}, "spec.ports.list[1]"),
        ({"direction": "ingress", "ports": {"from": 443.0, "to": 80.0}}, "spec.ports.to"),
        ({"direction": "ingress", "protocol": "tcp", "icmp": {"type": 8.0, "code": 0.0}}, "spec.icmp"),
        ({"direction": "ingress", "protocol": "icmp", "ports": {"from": 22.0}}, "spec.ports"),
        ({"direction": "ingress", "protocol": "icmp", "icmp": {"type": 9.0, "code": 0.0}}, "spec.icmp.type"),
        ({"direction": "ingress", "protocol": "icmp", "icmp": {"type": 8.0, "code": 6.0}}, "spec.icmp.code"),
    ],
)
def test_security_group_rule_rejected_values_flagged(spec, field):
    violations = rule_valid_security_group_rule({"spec": spec})
    assert _fields(violations) == [field]
    assert violations[0].severity == "HIGH"


def test_icmp_rule_without_protocol_is_valid():
    assert rule_valid_security_group_rule({"spec": {"direction": "egress", "icmp": {"type": 8.0, "code": 0.0}}}) == []


def test_port_range_and_list_valid():
    spec = {"direction": "ingress", "ports": {"from": 1.0, "to": 65535.0, "list": [80.0, 443.0]}}
    assert rule_valid_security_group_rule({"spec": spec}) == []


def test_inline_rules_get_the_same_checks():
    inputs = {"spec": {"rules": [GOOD_RULE, {"direction": "ingress", "protocol": "udpp"}]}}
    assert _fields(rule_inline_security_group_rules(inputs)) == [
        "spec.rules[1].sourceRef",
        "spec.rules[1].protocol",
    ]


def test_security_group_without_inline_rules_passes():
    assert rule_inline_security_group_rules({"spec": {}}) == []
    assert rule_inline_security_group_rules({"spec": {"ruleRefs": [{"resource": "security-group-rules/r"}]}}) == []


# ---------------------------------------------------------------------------
# Role and RoleAssignment
# ---------------------------------------------------------------------------


def test_scoped_role_passes():
    spec = {"permissions": [{"provider": "seca.storage/v1", "resources": ["images/*"], "verb": ["get", "list"]}]}
    assert rule_no_wildcard_role_resources({"spec": spec}) == []


def test_wildcard_role_resources_flagged():
    spec = {
        "permissions": [
            {"provider": "seca.storage/v1", "resources": ["images/*"], "verb": ["get"]},
            {"provider": "seca.compute/v1", "resources": ["*"], "verb": ["get", "delete"]},
        ]
    }
    violations = rule_no_wildcard_role_resources({"spec": spec})
    assert _fields(violations) == ["spec.permissions[1].resources"]
    assert violations[0].severity == "HIGH"


GOOD_ASSIGNMENT = {"subs": ["user1@example.com"], "roles": ["viewer"], "scopes": [{"workspaces": ["ws-1"]}]}


def test_scoped_role_assignment_passes():
    inputs = {"spec": GOOD_ASSIGNMENT}
    assert rule_no_wildcard_role_assignment(inputs) == []
    assert rule_scoped_role_assignment(inputs) == []


def test_role_assignment_to_every_user_flagged():
    violations = rule_no_wildcard_role_assignment({"spec": {**GOOD_ASSIGNMENT, "subs": ["*"]}})
    assert _fields(violations) == ["spec.subs"]
    assert violations[0].severity == "HIGH"


def test_role_assignment_to_every_tenant_flagged():
    spec = {**GOOD_ASSIGNMENT, "scopes": [{"workspaces": ["ws-1"]}, {"workspaces": ["ws-2"], "tenants": ["*"]}]}
    violations = rule_no_wildcard_role_assignment({"spec": spec})
    assert _fields(violations) == ["spec.scopes[1].tenants"]
    assert violations[0].severity == "HIGH"


def test_role_assignment_scope_without_workspaces_flagged():
    violations = rule_scoped_role_assignment({"spec": {**GOOD_ASSIGNMENT, "scopes": [{"regions": ["eu-1"]}]}})
    assert _fields(violations) == ["spec.scopes[0].workspaces"]
    assert violations[0].severity == "MEDIUM"


def test_role_assignment_without_scopes_flagged():
    violations = rule_scoped_role_assignment({"spec": {**GOOD_ASSIGNMENT, "scopes": []}})
    assert _fields(violations) == ["spec.scopes"]
    assert violations[0].severity == "HIGH"


# ---------------------------------------------------------------------------
# Security group on network-facing resources
# ---------------------------------------------------------------------------


def test_public_nic_with_security_group_passes():
    spec = {"publicIpRefs": [{"resource": "public-ips/ip"}], "securityGroupRefs": [SG]}
    assert rule_public_nic_security_group({"spec": spec}) == []


def test_private_nic_without_security_group_passes():
    assert rule_public_nic_security_group({"spec": {"addresses": ["10.0.0.5"]}}) == []


def test_public_nic_without_security_group_flagged():
    violations = rule_public_nic_security_group({"spec": {"publicIpRefs": [{"resource": "public-ips/ip"}]}})
    assert _fields(violations) == ["spec.securityGroupRefs"]
    assert violations[0].severity == "HIGH"


@pytest.mark.parametrize(
    "rule, field, spec_with_group",
    [
        (rule_load_balancer_security_group, "spec.securityGroupRef", {"securityGroupRef": SG}),
        (rule_nat_gateway_security_group, "spec.securityGroupRef", {"securityGroupRef": SG}),
        (
            rule_node_pool_security_group,
            "spec.nodeTemplate.securityGroupRef",
            {"nodeTemplate": {"securityGroupRef": SG}},
        ),
    ],
)
def test_missing_security_group_flagged(rule, field, spec_with_group):
    assert rule({"spec": spec_with_group}) == []
    violations = rule({"spec": {}})
    assert _fields(violations) == [field]
    assert violations[0].severity == "MEDIUM"


# ---------------------------------------------------------------------------
# Values the provider rejects at admission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_supported_image_architecture_passes(arch):
    assert rule_valid_image_architecture({"spec": {"cpuArchitecture": arch}}) == []


@pytest.mark.parametrize("arch", [None, "x86_64", "riscv64"])
def test_unsupported_image_architecture_flagged(arch):
    violations = rule_valid_image_architecture({"spec": {"cpuArchitecture": arch}})
    assert _fields(violations) == ["spec.cpuArchitecture"]
    assert violations[0].severity == "HIGH"


def test_node_root_volume_at_minimum_passes():
    assert rule_node_root_volume_size({"spec": {"nodeTemplate": {"rootVolume": {"sizeGB": 20.0}}}}) == []


def test_node_root_volume_below_minimum_flagged():
    violations = rule_node_root_volume_size({"spec": {"nodeTemplate": {"rootVolume": {"sizeGB": 10.0}}}})
    assert _fields(violations) == ["spec.nodeTemplate.rootVolume.sizeGB"]
    assert violations[0].severity == "HIGH"


# ---------------------------------------------------------------------------
# Registration and end-to-end fixtures
# ---------------------------------------------------------------------------


def _check_program(schema_path, program):
    mg = MockGenerator(schema_path)
    results = mg.run_with_mocks(str(program))
    oracle = CorrectnessOracle(schema_path)
    cape.register(oracle)
    found = {}
    for name, outputs in results.items():
        resource_type = mg.get_resource_type(name)
        assert resource_type, f"no type token recorded for {name}"
        found[name] = oracle.check(resource_type, outputs)
    return found


def test_every_rule_is_registered_on_a_resource_type():
    oracle = CorrectnessOracle([])
    cape.register(oracle)
    registered = {fn for _, fn in oracle.registered_rules()}
    public = {getattr(cape, n) for n in dir(cape) if n.startswith("rule_")}
    assert registered == public
    assert all(scope and scope.startswith("cape:") for scope, _ in oracle.registered_rules())


def test_secure_stack_has_no_violations(cape_schema_path, cape_fixtures):
    found = _check_program(cape_schema_path, cape_fixtures / "path_a_secure_stack.py")
    assert len(found) == 11  # every resource captured, so the empty result below is not vacuous
    assert {name: _fields(v) for name, v in found.items() if v} == {}


def test_insecure_stack_is_caught_by_every_rule(cape_schema_path, cape_fixtures):
    found = _check_program(cape_schema_path, cape_fixtures / "path_a_insecure_stack.py")
    fields = {name: _fields(v) for name, v in found.items() if v}
    assert fields == {
        "api-cluster": ["spec.restrictKubernetesApi"],
        "nodes": ["spec.nodeTemplate.securityGroupRef", "spec.nodeTemplate.rootVolume.sizeGB"],
        "allow-any": ["spec.sourceRef", "spec.protocol"],
        "bad-ports": ["spec.ports.to"],
        "web-sg": ["spec.rules[0].sourceRef"],
        "admin": ["spec.permissions[0].resources"],
        "everyone-admin": ["spec.subs", "spec.scopes[0].workspaces"],
        "public-nic": ["spec.securityGroupRefs"],
        "lb": ["spec.securityGroupRef"],
        "nat": ["spec.securityGroupRef"],
        "image": ["spec.cpuArchitecture"],
        "vm": ["spec.sshKeys"],
    }
