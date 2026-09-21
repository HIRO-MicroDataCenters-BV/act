#!/usr/bin/env python
"""Regenerate the CAPE provider schema from the installed pulumi_cape SDK.

The CAPE provider publishes no plugin binary, so `pulumi package get-schema cape` cannot
reach it the way `schema_resolver` reaches other providers. The Python SDK is generated
from the provider, so its resource classes are the closest source of truth available: the
type token comes from the `@pulumi.type_token` decorator, input properties and required
inputs from the matching `...Args` constructor, and output properties from the
`@pulumi.getter` properties.

Run it after bumping the `pulumi-cape` pin in pyproject.toml, then commit both copies:

    uv run python tools/gen_cape_schema.py \\
        tests/fixtures/cape/schema.json workflow/act-check/schemas/cape.json
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import re
import sys
from typing import Any, Iterator

import pulumi
import pulumi_cape
from pulumi.type_token import get_type_token

# Neither holds resources: `config` is provider settings, `schemas` the shared arg types.
SKIP_PACKAGES = {"config", "schemas"}

# The generated resource classes carry no docstring of their own, so descriptions are
# derived from the class name. These three were written by hand and are kept verbatim.
DESCRIPTIONS = {
    "cape:workspace:Workspace": "A CAPE workspace grouping resources under a tenant",
    "cape:compute:Instance": "A CAPE compute instance (virtual machine)",
    "cape:storage:BlockStorage": "A CAPE block storage volume",
}

# Checked in order: a Mapping[str, str] must not be read as a string.
TYPE_PATTERNS = (
    (r"\b(?:Mapping|Dict)\b", "object"),
    (r"\b(?:Sequence|List)\b", "array"),
    (r"\bbool\b", "boolean"),
    (r"\bint\b", "integer"),
    (r"\bfloat\b", "number"),
    (r"\bstr\b", "string"),
)


def describe(token: str, class_name: str) -> str:
    if token in DESCRIPTIONS:
        return DESCRIPTIONS[token]
    return "A CAPE " + re.sub(r"(?<!^)(?=[A-Z])", " ", class_name).lower()


def json_type(annotation: Any) -> str:
    """Map a Python annotation onto the JSON-schema type name the ACT schema uses.

    Matching is textual because the generated SDK nests annotations several layers deep
    (`pulumi.Input[Optional[Mapping[str, pulumi.Input[str]]]]`) and only the outermost
    shape decides the type. Anything unrecognised is one of the generated schema
    dataclasses, which serialise as objects.
    """
    text = str(annotation)
    for pattern, name in TYPE_PATTERNS:
        if re.search(pattern, text):
            return name
    return "object"


def resource_modules() -> Iterator[Any]:
    base = pulumi_cape.__path__[0]
    for package in pkgutil.iter_modules([base]):
        if not package.ispkg or package.name in SKIP_PACKAGES:
            continue
        subpackage = importlib.import_module(f"pulumi_cape.{package.name}")
        for module in pkgutil.iter_modules(subpackage.__path__):
            yield importlib.import_module(f"pulumi_cape.{package.name}.{module.name}")


def collect() -> dict:
    resources: dict = {}
    for module in resource_modules():
        for name, obj in vars(module).items():
            if not inspect.isclass(obj) or not issubclass(obj, pulumi.CustomResource):
                continue
            # Skip the base class and anything merely imported into this module.
            if obj is pulumi.CustomResource or obj.__module__ != module.__name__:
                continue
            token = get_type_token(obj)
            args_cls = getattr(module, f"{name}Args", None)
            if not token or args_cls is None:
                continue

            input_properties = {}
            required = []
            for param in inspect.signature(args_cls.__init__).parameters.values():
                if param.name in ("__self__", "self"):
                    continue
                input_properties[param.name] = {"type": json_type(param.annotation)}
                if param.default is inspect.Parameter.empty:
                    required.append(param.name)

            properties = {
                prop_name: {"type": json_type(prop.fget.__annotations__.get("return", ""))}
                for prop_name, prop in vars(obj).items()
                if isinstance(prop, property) and prop.fget is not None
            }

            resources[token] = {
                "description": describe(token, name),
                "inputProperties": dict(sorted(input_properties.items())),
                "requiredInputs": sorted(required),
                "properties": dict(sorted(properties.items())),
            }
    return dict(sorted(resources.items()))


def main(argv: list[str] | None = None) -> int:
    targets = list(argv if argv is not None else sys.argv[1:])
    resources = collect()
    if not resources:
        print("no CAPE resources found - is pulumi_cape installed?", file=sys.stderr)
        return 1

    text = json.dumps({"name": "cape", "version": "0.1.0", "resources": resources}, indent=2) + "\n"
    if not targets:
        sys.stdout.write(text)
    for target in targets:
        with open(target, "w") as handle:
            handle.write(text)
        print(f"wrote {len(resources)} resources to {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
