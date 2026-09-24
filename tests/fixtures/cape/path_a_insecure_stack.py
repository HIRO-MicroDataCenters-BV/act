# Simulates a misconfigured LLM-generated CAPE stack - Path A
# Every resource carries one of the misconfigurations the CAPE rules catch.
from pulumi_cape.authorization import Role, RoleAssignment
from pulumi_cape.compute import Instance
from pulumi_cape.kubernetes import KubernetesCluster, KubernetesNodePool
from pulumi_cape.loadbalancer import NetworkLoadBalancer
from pulumi_cape.natgateway import InternetNatGatewayInstance
from pulumi_cape.network import Nic, SecurityGroup, SecurityGroupRule
from pulumi_cape.schemas import (
    ImageSpecArgs,
    InstanceSpecArgs,
    InternetNatGatewayInstanceSpecArgs,
    KubernetesClusterSpecArgs,
    KubernetesNodePoolSpecArgs,
    KubernetesNodeRootVolumeArgs,
    KubernetesNodeTemplateArgs,
    LoadBalancerTargetArgs,
    NetworkLoadBalancerFrontendArgs,
    NetworkLoadBalancerSpecArgs,
    NicSpecArgs,
    PermissionArgs,
    PortsArgs,
    ReferenceArgs,
    RoleAssignmentScopeArgs,
    RoleAssignmentSpecArgs,
    RoleSpecArgs,
    SecurityGroupRuleSpecArgs,
    SecurityGroupSpecArgs,
    VolumeReferenceArgs,
)
from pulumi_cape.storage import Image

# Kubernetes API left open: no restrictKubernetesApi
KubernetesCluster("api-cluster", spec=KubernetesClusterSpecArgs(sku_ref=ReferenceArgs(resource="skus/k8s-standard")))

# Nodes with no security group and a root volume below the 20 GB minimum
KubernetesNodePool(
    "nodes",
    cluster="api-cluster",
    spec=KubernetesNodePoolSpecArgs(
        instances=3,
        node_template=KubernetesNodeTemplateArgs(
            root_volume=KubernetesNodeRootVolumeArgs(size_gb=10, sku_ref=ReferenceArgs(resource="skus/le500")),
            sku_ref=ReferenceArgs(resource="skus/xl"),
            subnet_ref=ReferenceArgs(resource="subnets/k8s-nodes"),
            zone="zone-1",
        ),
    ),
)

# Ingress from any source over any protocol
SecurityGroupRule("allow-any", spec=SecurityGroupRuleSpecArgs(direction="ingress"))

# Port range that ends below where it starts
SecurityGroupRule(
    "bad-ports",
    spec=SecurityGroupRuleSpecArgs(
        direction="ingress",
        protocol="tcp",
        ports=PortsArgs(from_=443, to=80),
        source_ref=[ReferenceArgs(resource="security-groups/frontend")],
    ),
)

# Inline rule open to any source
SecurityGroup(
    "web-sg",
    spec=SecurityGroupSpecArgs(
        rules=[SecurityGroupRuleSpecArgs(direction="ingress", protocol="tcp", ports=PortsArgs(from_=443))]
    ),
)

# Every compute resource
Role(
    "admin",
    spec=RoleSpecArgs(
        permissions=[PermissionArgs(provider="seca.compute/v1", resources=["*"], verb=["get", "delete"])]
    ),
)

# Every user, every workspace
RoleAssignment(
    "everyone-admin",
    spec=RoleAssignmentSpecArgs(subs=["*"], roles=["admin"], scopes=[RoleAssignmentScopeArgs(regions=["eu-1"])]),
)

# Public IP with no security group
Nic(
    "public-nic",
    spec=NicSpecArgs(
        addresses=["10.0.0.5"],
        subnet_ref=ReferenceArgs(resource="subnets/public"),
        public_ip_refs=[ReferenceArgs(resource="public-ips/web")],
    ),
)

NetworkLoadBalancer(
    "lb",
    spec=NetworkLoadBalancerSpecArgs(
        nic_ref=ReferenceArgs(resource="nics/lb"),
        frontends=[
            NetworkLoadBalancerFrontendArgs(
                port=443,
                protocol="tcp",
                target=LoadBalancerTargetArgs(members=[ReferenceArgs(resource="nics/web")]),
            )
        ],
    ),
)

InternetNatGatewayInstance(
    "nat",
    spec=InternetNatGatewayInstanceSpecArgs(
        nic_ref=ReferenceArgs(resource="nics/nat"),
        public_ip_ref=ReferenceArgs(resource="public-ips/nat"),
        zone="zone-1",
    ),
)

# Architecture the provider does not accept
Image(
    "image",
    spec=ImageSpecArgs(block_storage_ref=ReferenceArgs(resource="block-storages/golden"), cpu_architecture="x86_64"),
)

# SSH keys with no security group
Instance(
    "vm",
    spec=InstanceSpecArgs(
        boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="block-storages/boot")),
        sku_ref=ReferenceArgs(resource="skus/standard"),
        zone="zone-1",
        ssh_keys=["ssh-keys/ops"],
    ),
)
