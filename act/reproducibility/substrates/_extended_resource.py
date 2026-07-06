"""Declare a Kubernetes Extended Resource on a node.

Used by accelerator substrates (GPU, FPGA, CXL) that don't need real hardware; they just want the resource
schedulable so user IaC can request it.

Patches both `.status.capacity` and `.status.allocatable` via `--subresource=status`: kubelet doesn't
overwrite extended resources set through the status subresource, and the scheduler reads `allocatable`, so
patching only `capacity` leaves the resource unschedulable. Requires `kubectl` >= 1.24.
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
    """Advertise `resource_name: count` on the first node; waits for the API server and a registered node first."""
    node = _wait_for_node(kubeconfig, api_ready_timeout)

    # JSON Patch encodes "/" inside the resource name as "~1".
    encoded = resource_name.replace("/", "~1")
    value = str(count)
    patch = json.dumps(
        [
            {"op": "add", "path": f"/status/capacity/{encoded}", "value": value},
            {"op": "add", "path": f"/status/allocatable/{encoded}", "value": value},
        ]
    )
    subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            kubeconfig,
            "--insecure-skip-tls-verify",
            "patch",
            "node",
            node,
            "--subresource=status",
            "--type=json",
            "-p",
            patch,
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )


def _wait_for_node(kubeconfig: str, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                kubeconfig,
                "--insecure-skip-tls-verify",
                "get",
                "nodes",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            name = result.stdout.decode().strip()
            if name:
                return name
        last_err = result.stderr.decode().strip()
        time.sleep(2)
    raise TimeoutError(
        "kubectl get nodes did not return a registered node within " f"{timeout}s (last stderr: {last_err!r})"
    )
