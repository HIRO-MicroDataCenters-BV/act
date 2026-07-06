# Simulates a valid LLM-generated CAPE program - Path A
import pulumi
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs
from pulumi_cape.workspace import Workspace

ws = Workspace("my-workspace", spec={})

instance = Instance(
    "my-instance",
    spec=InstanceSpecArgs(
        boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot-vol")),
        sku_ref=ReferenceArgs(resource="skus/standard"),
        zone="zone-1",
        security_group_ref=ReferenceArgs(resource="security-groups/default"),
        ssh_keys=["ssh-keys/my-key"],
    ),
    workspace=ws.metadata.apply(lambda m: m["name"]),
)

pulumi.export("instance_status", instance.status)
