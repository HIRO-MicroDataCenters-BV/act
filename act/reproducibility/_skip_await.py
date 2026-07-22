"""Pulumi stack transformation that disables the k8s provider's readiness await.

`runtime_check` inlines this function's source into the wrapper `__main__` it runs for each
`pulumi up` (see `_SKIP_AWAIT_WRAPPER`), so `up` returns as soon as the API accepts each
manifest — the deployment-accepted comparison never waits for the workload to run. The
function is inlined (not imported) so the Pulumi program subprocess pulls in no `act`
package; keep it self-contained (only `pulumi` + builtins, annotations string-only).
"""

from __future__ import annotations

from typing import Optional

import pulumi


def skip_await_transformation(
    args: pulumi.ResourceTransformationArgs,
) -> Optional[pulumi.ResourceTransformationResult]:
    """Stamp `pulumi.com/skipAwait=true` on k8s resources with plain metadata; no-op otherwise."""
    if not args.type_.startswith("kubernetes:"):
        return None
    metadata = args.props.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return None
    metadata = dict(metadata or {})
    annotations = metadata.get("annotations")
    if annotations is not None and not isinstance(annotations, dict):
        return None
    annotations = dict(annotations or {})
    annotations["pulumi.com/skipAwait"] = "true"
    metadata["annotations"] = annotations
    props = dict(args.props)
    props["metadata"] = metadata
    return pulumi.ResourceTransformationResult(props=props, opts=args.opts)
