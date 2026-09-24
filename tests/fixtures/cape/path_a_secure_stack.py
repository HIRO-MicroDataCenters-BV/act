# Simulates a well-configured LLM-generated CAPE stack - Path A
# Every resource type a CAPE rule covers, configured so no rule fires.
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

SG = ReferenceArgs(resource="security-groups/web")
HTTPS_FROM_FRONTEND = SecurityGroupRuleSpecArgs(
    direction="ingress",
    protocol="tcp",
    ports=PortsArgs(from_=443),
    source_ref=[ReferenceArgs(resource="security-groups/frontend")],
)

KubernetesCluster(
    "api-cluster",
    spec=KubernetesClusterSpecArgs(
        sku_ref=ReferenceArgs(resource="skus/k8s-standard"),
        restrict_kubernetes_api=["10.0.0.0/8"],
    ),
)

KubernetesNodePool(
    "nodes",
    cluster="api-cluster",
    spec=KubernetesNodePoolSpecArgs(
        instances=3,
        node_template=KubernetesNodeTemplateArgs(
            root_volume=KubernetesNodeRootVolumeArgs(size_gb=100, sku_ref=ReferenceArgs(resource="skus/le500")),
            sku_ref=ReferenceArgs(resource="skus/xl"),
            subnet_ref=ReferenceArgs(resource="subnets/k8s-nodes"),
            zone="zone-1",
            security_group_ref=ReferenceArgs(resource="security-groups/k8s-nodes"),
        ),
    ),
)

SecurityGroupRule("https-in", spec=HTTPS_FROM_FRONTEND)

SecurityGroup("web-sg", spec=SecurityGroupSpecArgs(rules=[HTTPS_FROM_FRONTEND]))

Role(
    "image-reader",
    spec=RoleSpecArgs(
        permissions=[PermissionArgs(provider="seca.storage/v1", resources=["images/*"], verb=["get", "list"])]
    ),
)

RoleAssignment(
    "reader-binding",
    spec=RoleAssignmentSpecArgs(
        subs=["user1@example.com"],
        roles=["image-reader"],
        scopes=[RoleAssignmentScopeArgs(workspaces=["ws-1"])],
    ),
)

Nic(
    "public-nic",
    spec=NicSpecArgs(
        addresses=["10.0.0.5"],
        subnet_ref=ReferenceArgs(resource="subnets/public"),
        public_ip_refs=[ReferenceArgs(resource="public-ips/web")],
        security_group_refs=[SG],
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
        security_group_ref=SG,
    ),
)

InternetNatGatewayInstance(
    "nat",
    spec=InternetNatGatewayInstanceSpecArgs(
        nic_ref=ReferenceArgs(resource="nics/nat"),
        public_ip_ref=ReferenceArgs(resource="public-ips/nat"),
        zone="zone-1",
        security_group_ref=ReferenceArgs(resource="security-groups/nat"),
    ),
)

Image(
    "image",
    spec=ImageSpecArgs(block_storage_ref=ReferenceArgs(resource="block-storages/golden"), cpu_architecture="amd64"),
)

Instance(
    "vm",
    spec=InstanceSpecArgs(
        boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="block-storages/boot")),
        sku_ref=ReferenceArgs(resource="skus/standard"),
        zone="zone-1",
        security_group_ref=SG,
        ssh_keys=["ssh-keys/ops"],
    ),
)
