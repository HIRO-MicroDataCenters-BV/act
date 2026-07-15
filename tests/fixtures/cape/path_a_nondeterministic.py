# Nondeterministic CAPE program - captures uuid.uuid4() into the resource name on every run.
import uuid

from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs

Instance(
    f"inst-{uuid.uuid4()}",
    spec=InstanceSpecArgs(
        boot_volume=VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot-vol")),
        sku_ref=ReferenceArgs(resource="skus/standard"),
        zone="zone-1",
        security_group_ref=ReferenceArgs(resource="security-groups/default"),
    ),
    workspace="default",
)
