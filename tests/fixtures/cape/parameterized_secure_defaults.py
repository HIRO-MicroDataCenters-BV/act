# Parameterized CAPE program, secure by default.
# With no environment set, the instance has a security group. Setting CAPE_SECURITY_GROUP_REF
# to an empty string removes it while the SSH key stays configured: a single deploy with the
# defaults passes, and only varying the inputs exposes SSH.
import os

import pulumi
from pulumi_cape.compute import Instance
from pulumi_cape.schemas import InstanceSpecArgs, ReferenceArgs, VolumeReferenceArgs

spec_kwargs: dict = {
    "boot_volume": VolumeReferenceArgs(device_ref=ReferenceArgs(resource="volumes/boot-vol")),
    "sku_ref": ReferenceArgs(resource="skus/standard"),
    "zone": os.environ.get("CAPE_ZONE", "zone-1"),
}

ssh_keys = os.environ.get("CAPE_SSH_KEYS", "ssh-keys/ops")
if ssh_keys:
    spec_kwargs["ssh_keys"] = [ssh_keys]

security_group = os.environ.get("CAPE_SECURITY_GROUP_REF", "security-groups/web")
if security_group:
    spec_kwargs["security_group_ref"] = ReferenceArgs(resource=security_group)

instance = Instance("web-vm", spec=InstanceSpecArgs(**spec_kwargs), workspace="default-workspace")

pulumi.export("instance_status", instance.status)
