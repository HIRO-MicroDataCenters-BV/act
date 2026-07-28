"""Pulumi stack transformation that disables the k8s provider's readiness await.

`runtime_check` inlines this function's source into the wrapper `__main__` it runs for each
`pulumi up` (see `_skip_await_wrapper`), so `up` returns as soon as the API accepts each
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
    """Stamp `pulumi.com/skipAwait=true` on every k8s resource, resolving computed
    metadata/annotations via Output.apply so nothing is silently left awaiting."""
    if not args.type_.startswith("kubernetes:"):
        return None

    def _resolve(value, fn):
        # Plain/absent -> transform inline; a computed Output -> transform inside apply
        # so nothing is silently left awaiting.
        if isinstance(value, dict) or value is None:
            return fn(value)
        return pulumi.Output.from_input(value).apply(fn)

    def _stamp_annotations(annotations):
        annotations = dict(annotations or {})
        annotations["pulumi.com/skipAwait"] = "true"
        return annotations

    def _stamp_metadata(metadata):
        metadata = dict(metadata or {})
        metadata["annotations"] = _resolve(metadata.get("annotations"), _stamp_annotations)
        return metadata

    props = dict(args.props)
    props["metadata"] = _resolve(args.props.get("metadata"), _stamp_metadata)
    return pulumi.ResourceTransformationResult(props=props, opts=args.opts)
