"""CAPE rules: (inputs: dict) -> List[Violation].

Pulumi serializes the SDK's snake_case args to camelCase under the "spec" key
(e.g. securityGroupRef, sshKeys), and numbers arrive as floats. Only add rules for fields
that exist in the CAPE SDK; defaults and allowed values follow the SecAPI spec the SDK is
generated from, since the generated provider schema types every spec as a bare object.
"""

from typing import Callable, Iterator, List, Optional, Tuple

from act.core.violations import Violation

WORLD_CIDRS = ("0.0.0.0/0", "::/0")
RULE_DIRECTIONS = ("ingress", "egress")
RULE_PROTOCOLS = ("tcp", "udp", "tcp+udp", "icmp")
IP_VERSIONS = ("IPv4", "IPv6")
PORT_RANGE = (1, 65535)
ICMP_TYPE_RANGE = (0, 8)
ICMP_CODE_RANGE = (0, 5)
IMAGE_ARCHITECTURES = ("amd64", "arm64")
NODE_ROOT_VOLUME_MIN_GB = 20


def _spec(inputs: dict) -> dict:
    return inputs.get("spec") or {}


def _number(value) -> Optional[float]:
    """The value as a number, or None when it is not one (bool is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _is_int_in(value, bounds: Tuple[int, int]) -> bool:
    n = _number(value)
    return n is not None and n == int(n) and bounds[0] <= n <= bounds[1]


def _missing_security_group(spec: dict, key: str, field: str, what: str) -> List[Violation]:
    if spec.get(key):
        return []
    return [Violation(field=field, message=f"{what} has no security group attached", severity="MEDIUM")]


# ---------------------------------------------------------------------------
# Instance
# ---------------------------------------------------------------------------


def rule_no_exposed_instance(inputs: dict) -> List[Violation]:
    """securityGroupRef must be present in Instance spec."""
    spec = _spec(inputs)
    # With SSH keys set, rule_no_unprotected_ssh reports the same gap more specifically.
    if spec.get("securityGroupRef") or spec.get("sshKeys"):
        return []
    return [
        Violation(
            field="spec.securityGroupRef",
            message="Instance has no security group attached - its traffic is not filtered by one",
            severity="HIGH",
        )
    ]


def rule_no_unprotected_ssh(inputs: dict) -> List[Violation]:
    """sshKeys without securityGroupRef leaves SSH unrestricted by a security group."""
    spec = _spec(inputs)
    if spec.get("sshKeys") and not spec.get("securityGroupRef"):
        return [
            Violation(
                field="spec.sshKeys",
                message="SSH keys configured but no security group - SSH is not restricted to known sources",
                severity="HIGH",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# KubernetesCluster and KubernetesNodePool
# ---------------------------------------------------------------------------


def rule_restricted_kubernetes_api(inputs: dict) -> List[Violation]:
    """restrictKubernetesApi must name the sources allowed to reach the Kubernetes API."""
    allowed = _spec(inputs).get("restrictKubernetesApi") or []
    field = "spec.restrictKubernetesApi"
    if not allowed:
        message = "Kubernetes API is reachable from any IP address - set restrictKubernetesApi"
        return [Violation(field=field, message=message, severity="HIGH")]
    world = [c for c in allowed if isinstance(c, str) and c.strip() in WORLD_CIDRS]
    if world:
        message = f"Kubernetes API allows {', '.join(world)} - reachable from any IP address"
        return [Violation(field=field, message=message, severity="HIGH")]
    return []


def rule_node_pool_security_group(inputs: dict) -> List[Violation]:
    """Node pool nodeTemplate should reference a security group."""
    template = _spec(inputs).get("nodeTemplate") or {}
    return _missing_security_group(template, "securityGroupRef", "spec.nodeTemplate.securityGroupRef", "Node pool")


def rule_node_root_volume_size(inputs: dict) -> List[Violation]:
    """Node root volume sizeGB must meet the provider minimum of 20."""
    root = (_spec(inputs).get("nodeTemplate") or {}).get("rootVolume") or {}
    size = _number(root.get("sizeGB"))
    if size is not None and size < NODE_ROOT_VOLUME_MIN_GB:
        return [
            Violation(
                field="spec.nodeTemplate.rootVolume.sizeGB",
                message=f"Node root volume must be at least {NODE_ROOT_VOLUME_MIN_GB} GB, got {size:g}",
                severity="HIGH",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# SecurityGroupRule (standalone) and SecurityGroup (inline rules)
# ---------------------------------------------------------------------------


def _check_ingress_source(rule: dict, at: str) -> List[Violation]:
    if rule.get("direction") == "ingress" and not rule.get("sourceRef"):
        message = "Ingress rule has no sourceRef - traffic from any source is allowed"
        return [Violation(field=f"{at}.sourceRef", message=message, severity="HIGH")]
    return []


def _check_ingress_protocol(rule: dict, at: str) -> List[Violation]:
    if rule.get("direction") == "ingress" and not rule.get("protocol"):
        message = "Ingress rule has no protocol - any network protocol is allowed"
        return [Violation(field=f"{at}.protocol", message=message, severity="MEDIUM")]
    return []


def _port_values(ports: dict, at: str) -> Iterator[Tuple[str, object]]:
    for key in ("from", "to"):
        if ports.get(key) is not None:
            yield f"{at}.ports.{key}", ports[key]
    for i, port in enumerate(ports.get("list") or []):
        yield f"{at}.ports.list[{i}]", port


def _check_rule_values(rule: dict, at: str) -> List[Violation]:
    """Values the provider rejects at admission (SecAPI enums, port range, CEL checks)."""
    found: List[Violation] = []

    def reject(field: str, message: str) -> None:
        found.append(Violation(field=field, message=message, severity="HIGH"))

    direction = rule.get("direction")
    if direction not in RULE_DIRECTIONS:
        reject(f"{at}.direction", f"Rule direction must be one of {', '.join(RULE_DIRECTIONS)}, got {direction!r}")
    protocol = rule.get("protocol")
    if protocol is not None and protocol not in RULE_PROTOCOLS:
        reject(f"{at}.protocol", f"Rule protocol must be one of {', '.join(RULE_PROTOCOLS)}, got {protocol!r}")
    version = rule.get("version")
    if version is not None and version not in IP_VERSIONS:
        reject(f"{at}.version", f"Rule version must be one of {', '.join(IP_VERSIONS)}, got {version!r}")

    ports = rule.get("ports")
    if ports and protocol == "icmp":
        reject(f"{at}.ports", "Rule ports are not allowed when protocol is icmp")
    elif ports:
        valid = True
        for field, port in _port_values(ports, at):
            if not _is_int_in(port, PORT_RANGE):
                valid = False
                reject(field, f"Port must be an integer from {PORT_RANGE[0]} to {PORT_RANGE[1]}, got {port!r}")
        low, high = _number(ports.get("from")), _number(ports.get("to"))
        if valid and low is not None and high is not None and high < low:
            reject(f"{at}.ports.to", f"Port range end {high:g} is below its start {low:g}")

    icmp = rule.get("icmp")
    if icmp and protocol is not None and protocol != "icmp":
        reject(f"{at}.icmp", "Rule icmp is only allowed when protocol is icmp")
    elif icmp:
        for key, bounds in (("type", ICMP_TYPE_RANGE), ("code", ICMP_CODE_RANGE)):
            if not _is_int_in(icmp.get(key), bounds):
                reject(f"{at}.icmp.{key}", f"ICMP {key} must be from {bounds[0]} to {bounds[1]}, got {icmp.get(key)!r}")
    return found


_RULE_CHECKS: Tuple[Callable[[dict, str], List[Violation]], ...] = (
    _check_ingress_source,
    _check_ingress_protocol,
    _check_rule_values,
)


def rule_ingress_rule_has_source(inputs: dict) -> List[Violation]:
    """Ingress rules must set sourceRef; without it traffic from any source is allowed."""
    return _check_ingress_source(_spec(inputs), "spec")


def rule_ingress_rule_has_protocol(inputs: dict) -> List[Violation]:
    """Ingress rules should set protocol; without it any network protocol is allowed."""
    return _check_ingress_protocol(_spec(inputs), "spec")


def rule_valid_security_group_rule(inputs: dict) -> List[Violation]:
    """Rule direction, protocol, version, ports and icmp must hold values the provider accepts."""
    return _check_rule_values(_spec(inputs), "spec")


def rule_inline_security_group_rules(inputs: dict) -> List[Violation]:
    """Inline SecurityGroup rules get the same checks as SecurityGroupRule resources."""
    found: List[Violation] = []
    for i, rule in enumerate(_spec(inputs).get("rules") or []):
        for check in _RULE_CHECKS:
            found.extend(check(rule or {}, f"spec.rules[{i}]"))
    return found


# ---------------------------------------------------------------------------
# Role and RoleAssignment
# ---------------------------------------------------------------------------


def rule_no_wildcard_role_resources(inputs: dict) -> List[Violation]:
    """Role permissions must not grant every resource with the "*" wildcard."""
    found = []
    for i, permission in enumerate(_spec(inputs).get("permissions") or []):
        permission = permission or {}
        if "*" in (permission.get("resources") or []):
            provider = permission.get("provider") or "the provider"
            found.append(
                Violation(
                    field=f"spec.permissions[{i}].resources",
                    message=f"Role grants every {provider} resource ('*') - scope it to a type such as 'images/*'",
                    severity="HIGH",
                )
            )
    return found


def rule_no_wildcard_role_assignment(inputs: dict) -> List[Violation]:
    """RoleAssignment must not assign roles to every user or open them to every tenant."""
    spec = _spec(inputs)
    found = []
    if "*" in (spec.get("subs") or []):
        found.append(
            Violation(
                field="spec.subs", message="Roles are assigned to every user of the tenant ('*')", severity="HIGH"
            )
        )
    for i, scope in enumerate(spec.get("scopes") or []):
        if "*" in ((scope or {}).get("tenants") or []):
            found.append(
                Violation(
                    field=f"spec.scopes[{i}].tenants",
                    message="Role assignment is opened to every tenant ('*')",
                    severity="HIGH",
                )
            )
    return found


def rule_scoped_role_assignment(inputs: dict) -> List[Violation]:
    """Each RoleAssignment scope should name the workspaces it applies to."""
    scopes = _spec(inputs).get("scopes") or []
    if not scopes:
        return [Violation(field="spec.scopes", message="Role assignment needs at least one scope", severity="HIGH")]
    return [
        Violation(
            field=f"spec.scopes[{i}].workspaces",
            message="Scope names no workspaces - the assignment is valid in every workspace",
            severity="MEDIUM",
        )
        for i, scope in enumerate(scopes)
        if not (scope or {}).get("workspaces")
    ]


# ---------------------------------------------------------------------------
# Security group on network-facing resources
# ---------------------------------------------------------------------------


def rule_public_nic_security_group(inputs: dict) -> List[Violation]:
    """A NIC with a public IP must reference a security group."""
    spec = _spec(inputs)
    if spec.get("publicIpRefs") and not spec.get("securityGroupRefs"):
        return [
            Violation(
                field="spec.securityGroupRefs",
                message="NIC has a public IP but no security group attached",
                severity="HIGH",
            )
        ]
    return []


def rule_load_balancer_security_group(inputs: dict) -> List[Violation]:
    """Network load balancer should reference a security group."""
    return _missing_security_group(_spec(inputs), "securityGroupRef", "spec.securityGroupRef", "Load balancer")


def rule_nat_gateway_security_group(inputs: dict) -> List[Violation]:
    """NAT gateway should reference a security group."""
    return _missing_security_group(_spec(inputs), "securityGroupRef", "spec.securityGroupRef", "NAT gateway")


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


def rule_valid_image_architecture(inputs: dict) -> List[Violation]:
    """Image cpuArchitecture must be one the provider accepts (amd64, arm64)."""
    arch = _spec(inputs).get("cpuArchitecture")
    if arch in IMAGE_ARCHITECTURES:
        return []
    return [
        Violation(
            field="spec.cpuArchitecture",
            message=f"Image cpuArchitecture must be one of {', '.join(IMAGE_ARCHITECTURES)}, got {arch!r}",
            severity="HIGH",
        )
    ]


RULES = {
    "cape:compute:Instance": (rule_no_exposed_instance, rule_no_unprotected_ssh),
    "cape:kubernetes:KubernetesCluster": (rule_restricted_kubernetes_api,),
    "cape:kubernetes:KubernetesNodePool": (rule_node_pool_security_group, rule_node_root_volume_size),
    "cape:network:SecurityGroupRule": (
        rule_ingress_rule_has_source,
        rule_ingress_rule_has_protocol,
        rule_valid_security_group_rule,
    ),
    "cape:network:SecurityGroup": (rule_inline_security_group_rules,),
    "cape:authorization:Role": (rule_no_wildcard_role_resources,),
    "cape:authorization:RoleAssignment": (rule_no_wildcard_role_assignment, rule_scoped_role_assignment),
    "cape:network:Nic": (rule_public_nic_security_group,),
    "cape:loadbalancer:NetworkLoadBalancer": (rule_load_balancer_security_group,),
    "cape:natgateway:InternetNatGatewayInstance": (rule_nat_gateway_security_group,),
    "cape:storage:Image": (rule_valid_image_architecture,),
}


def register(oracle) -> None:
    for resource_type, rules in RULES.items():
        for rule in rules:
            oracle.add_rule(rule, resource_type=resource_type)
