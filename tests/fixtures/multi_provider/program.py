"""Multi-provider fixture: uses CAPE Instance and pulumi-random RandomPassword in one program."""

import pulumi
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs
from pulumi_random import RandomPassword

instance = Instance(
    "my-instance",
    spec=InstanceSpecArgs(
        boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot-vol")),
        sku_ref=ReferenceArgs(resource="skus/standard"),
        zone="zone-1",
        security_group_ref=ReferenceArgs(resource="security-groups/default"),
    ),
    workspace="default",
)

password = RandomPassword(
    "db-password",
    length=24,
    special=True,
)

pulumi.export("instance_status", instance.status)
pulumi.export("password", password.result)
