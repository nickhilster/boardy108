"""Load the versioned visual canon and format local director instructions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate canon field: {key}")
        value[key] = item
    return value


def load_canon(project_dir: Path | str) -> dict[str, Any]:
    """Validate canon.json and hash its exact source bytes, including whitespace."""
    source = (Path(project_dir) / "canon.json").read_bytes()
    try:
        value = json.loads(source.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Canon must be valid UTF-8 JSON") from error
    fields = {"version", "locked_traits", "allowed_variations", "prohibitions"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Canon requires exactly version, locked_traits, allowed_variations, prohibitions")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported canon version; expected integer 1")
    for field in ("locked_traits", "allowed_variations", "prohibitions"):
        items = value[field]
        if not isinstance(items, list) or not items:
            raise ValueError(f"Canon {field} must be a non-empty array")
        if field == "locked_traits" and len(items) != 5:
            raise ValueError("Canon locked_traits must contain exactly five fixed traits")
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"Canon {field} must contain non-empty strings")
            if item.strip() in seen:
                raise ValueError(f"Canon {field} contains duplicate strings")
            seen.add(item.strip())
    return {**value, "sha256": hashlib.sha256(source).hexdigest()}


def format_instructions(value: dict[str, Any]) -> str:
    """Format validated canon without reordering or rewriting its source strings."""
    sections = [
        "BOARDY CANON:\nKeep all locked traits fixed; preserve the original scene brief. "
        "Let the allowed variation axes follow the scene brief while preserving the locked traits."
    ]
    for field, heading in (
        ("locked_traits", "LOCKED TRAITS"),
        ("allowed_variations", "ALLOWED VARIATIONS"),
        ("prohibitions", "PROHIBITIONS"),
    ):
        sections.append(f"{heading}:\n" + "\n".join(f"- {item}" for item in value[field]))
    return "\n\n".join(sections)
