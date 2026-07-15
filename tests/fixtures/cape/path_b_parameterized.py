# Parameterized CAPE program - Path B (developer-written)
# Reads CAPE_ZONE, CAPE_SSH_KEYS, CAPE_SECURITY_GROUP_REF from os.environ.
# Used as a Path B fixture for fuzz and property runners.
import os

import pulumi
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs
from pulumi_cape.workspace import Workspace

ws = Workspace("my-workspace", spec={})

spec_kwargs: dict = {
    "boot_volume": VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot-vol")),
    "sku_ref": ReferenceArgs(resource="skus/standard"),
    "zone": os.environ.get("CAPE_ZONE", "zone-1"),
}

ssh_keys_val = os.environ.get("CAPE_SSH_KEYS", "")
if ssh_keys_val:
    spec_kwargs["ssh_keys"] = [ssh_keys_val]

sgr_val = os.environ.get("CAPE_SECURITY_GROUP_REF", "")
if sgr_val:
    spec_kwargs["security_group_ref"] = ReferenceArgs(resource=sgr_val)

instance = Instance(
    "my-instance",
    spec=InstanceSpecArgs(**spec_kwargs),
    workspace=ws.metadata.apply(lambda m: m["name"]),
)

pulumi.export("instance_status", instance.status)
