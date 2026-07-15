# Simulates a misconfigured LLM-generated CAPE program - Path A
# Violation: ssh_keys present but security_group_ref absent (no firewall)
import pulumi
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs

instance = Instance(
    "my-instance",
    spec=InstanceSpecArgs(
        boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot-vol")),
        sku_ref=ReferenceArgs(resource="skus/standard"),
        zone="zone-1",
        ssh_keys=["ssh-keys/my-key"],
        # security_group_ref intentionally absent
    ),
    workspace="default-workspace",
)

pulumi.export("instance_status", instance.status)
