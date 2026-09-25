# Parameterized CAPE program: the range allowed to reach the Kubernetes API comes from the
# environment. The default is private; an empty value drops the restriction, and 0.0.0.0/0
# opens the API to the world. Only the second is found by trying real values, not just
# unset / empty / non-empty.
import os

from pulumi_cape.kubernetes import KubernetesCluster
from pulumi_cape.schemas import KubernetesClusterSpecArgs, ReferenceArgs

allowed = os.environ.get("CAPE_API_ALLOWED_CIDR", "10.0.0.0/8")

KubernetesCluster(
    "api-cluster",
    spec=KubernetesClusterSpecArgs(
        sku_ref=ReferenceArgs(resource="skus/k8s-standard"),
        restrict_kubernetes_api=[allowed] if allowed else None,
    ),
)
