"""Shared provider-schema loading."""

from __future__ import annotations

import json


def load_merged_resources(schema_path: str | list[str]) -> dict:
    """Merge the ``resources`` map from one or more provider schema JSON files.

    Returns ``{"resources": {token: schema, ...}}``; later files override earlier
    ones on token collision.
    """
    paths = [schema_path] if isinstance(schema_path, str) else list(schema_path)
    merged: dict = {}
    for p in paths:
        with open(p) as f:
            merged.update(json.load(f).get("resources", {}))
    return {"resources": merged}
