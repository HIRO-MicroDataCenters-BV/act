"""Shared helper: declare a Kubernetes Extended Resource on a node.

Used by accelerator-style substrates (GPU, FPGA) that don't need real
hardware on the host — they just want the resource to show up as
schedulable so user IaC programs can request it.

Mechanism: `kubectl patch node ... --subresource=status --type=json` on
both `.status.capacity` and `.status.allocatable`. Kubelet does not
overwrite extended resources set via the status subresource; the
scheduler picks resources from `allocatable`, so patching only
`capacity` leaves the resource unschedulable.

Requires `kubectl` ≥ 1.24 (for `--subresource=status`).
"""

from __future__ import annotations

import json
import subprocess
import time


def patch_node_extended_resource(
    kubeconfig: str,
    resource_name: str,
    count: int,
    *,
    api_ready_timeout: int = 60,
) -> None:
    """Advertise `resource_name: count` on the first node in the cluster.

    Waits for the API server to be reachable and a node to be registered
    before issuing the patch.
    """
    node = _wait_for_node(kubeconfig, api_ready_timeout)

    # JSON Patch encodes "/" inside the resource name as "~1".
    encoded = resource_name.replace("/", "~1")
    value = str(count)
    patch = json.dumps([
        {"op": "add", "path": f"/status/capacity/{encoded}", "value": value},
        {"op": "add", "path": f"/status/allocatable/{encoded}", "value": value},
    ])
    subprocess.run(
        [
            "kubectl", "--kubeconfig", kubeconfig,
            "--insecure-skip-tls-verify",
            "patch", "node", node,
            "--subresource=status", "--type=json",
            "-p", patch,
        ],
        capture_output=True, check=True, timeout=30,
    )


def _wait_for_node(kubeconfig: str, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "kubectl", "--kubeconfig", kubeconfig,
                "--insecure-skip-tls-verify",
                "get", "nodes",
                "-o", "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True, check=False, timeout=10,
        )
        if result.returncode == 0:
            name = result.stdout.decode().strip()
            if name:
                return name
        last_err = result.stderr.decode().strip()
        time.sleep(2)
    raise TimeoutError(
        f"kubectl get nodes did not return a registered node within "
        f"{timeout}s (last stderr: {last_err!r})"
    )
