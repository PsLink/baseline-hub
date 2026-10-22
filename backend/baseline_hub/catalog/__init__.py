"""Catalog types used to reconcile remote exports with GitHub snapshots.

AutoTune owns recipe conversion, export generation and schema validation.
"""

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CatalogModel:
    """What the target catalog publishes for a model at the reviewed commit."""

    name: str
    metadata: dict[str, Any]
    # metadata.yaml as published, for the comments it carries; "" when only
    # the index's copy of its fields could be read.
    metadata_text: str = ""
    # id → {"version": "1.0.2", "document": the variant file, when it could be read}
    variants: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class CatalogFile:
    path: str  # models/<name>/...
    kind: str  # variant | metadata | report
    text: str


def slug(text: str, limit: int = 63) -> str:
    """A catalog id: lowercase, [a-z0-9._-], starting alphanumeric."""
    out = re.sub(r"[^a-z0-9._-]+", "-", (text or "").lower()).strip("-._")
    return out[:limit].rstrip("-._")
