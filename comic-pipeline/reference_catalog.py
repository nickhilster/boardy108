"""Load and validate a production project's reference catalog and manifest.

Pure data-layer module: no Ollama or ComfyUI calls, so it stays unit-testable
without any local service running. See
docs/superpowers/specs/2026-09-19-production-reference-pipeline-design.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ALLOWED_KINDS = {"character", "location", "entity", "interface", "style"}
ALLOWED_STATUSES = {"draft", "approved", "retired"}
DEFAULT_MAX_RENDER_REFERENCES = 3
BOARDY_BATCH_TITLES = (
    "First contact", "Cartography", "Work", "Curiosity", "Memory",
    "Misreadings", "Idle", "Craft", "The box",
)


class CatalogError(ValueError):
    """One or more problems were found in a references.json or production.json file."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _resolve_project_path(project_dir: Path, relative: str, *, field: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise CatalogError([f"{field} must be a non-empty string"])
    candidate = Path(relative)
    if candidate.is_absolute():
        raise CatalogError([f"{field} must be a project-relative path, not absolute: {relative}"])
    resolved = (project_dir / candidate).resolve()
    try:
        resolved.relative_to(project_dir.resolve())
    except ValueError as error:
        raise CatalogError([f"{field} escapes the project directory: {relative}"]) from error
    return resolved


def load_catalog(project_dir: Path | str) -> dict[str, dict[str, Any]]:
    """Load and validate references.json. Returns {id: entry} with a resolved 'file' path added."""
    project = Path(project_dir).resolve()
    path = project / "references.json"
    if not path.is_file():
        raise CatalogError([f"Reference catalog does not exist: {path}"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CatalogError([f"{path} is not valid JSON: {error}"]) from error

    errors: list[str] = []
    if not isinstance(data, dict) or data.get("version") != 1:
        errors.append(f"{path}: unsupported or missing 'version' (expected 1)")
    references = data.get("references") if isinstance(data, dict) else None
    if not isinstance(references, dict):
        raise CatalogError(errors + [f"{path}: 'references' must be an object"])

    catalog: dict[str, dict[str, Any]] = {}
    for reference_id, entry in references.items():
        if not isinstance(entry, dict):
            errors.append(f"reference '{reference_id}': entry must be an object")
            continue
        kind = entry.get("kind")
        status = entry.get("status")
        if kind not in ALLOWED_KINDS:
            errors.append(f"reference '{reference_id}': kind must be one of {sorted(ALLOWED_KINDS)}, got {kind!r}")
        if status not in ALLOWED_STATUSES:
            errors.append(f"reference '{reference_id}': status must be one of {sorted(ALLOWED_STATUSES)}, got {status!r}")
        try:
            resolved = _resolve_project_path(project, entry.get("path"), field=f"reference '{reference_id}'.path")
        except CatalogError as error:
            errors.extend(error.errors)
            continue
        # A draft entry may be a pure placeholder recorded before its asset
        # exists (see the production reference gates); approved/retired
        # entries must point at a real file.
        if not resolved.is_file() and status != "draft":
            errors.append(f"reference '{reference_id}': file does not exist: {resolved}")
        catalog[reference_id] = {**entry, "file": resolved}

    if errors:
        raise CatalogError(errors)
    return catalog


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _load_batches(data: dict[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    """Validate the ordered Boardy batch definitions without inferring membership."""
    batches = data.get("batches")
    if not isinstance(batches, list) or not batches:
        errors.append("'batches' must be a non-empty array of nine batches")
        return []
    if len(batches) != 9:
        errors.append("Boardy production requires exactly nine batches")
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    resolved = []
    for index, batch in enumerate(batches):
        prefix = f"batch[{index}]"
        if not isinstance(batch, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        batch_id, title, order = batch.get("id"), batch.get("title"), batch.get("order")
        if not isinstance(batch_id, str) or not batch_id.strip():
            errors.append(f"{prefix}: 'id' must be a non-empty string")
        elif batch_id in seen_ids:
            errors.append(f"{prefix}: duplicate batch ID '{batch_id}'")
        else:
            seen_ids.add(batch_id)
        if not _positive_integer(order):
            errors.append(f"{prefix}: 'order' must be a positive integer")
        else:
            if order in seen_orders:
                errors.append(f"{prefix}: duplicate batch order {order}")
            seen_orders.add(order)
            if order != index + 1:
                errors.append(f"{prefix}: batch order must increase from 1 through 9")
        if index >= len(BOARDY_BATCH_TITLES) or title != BOARDY_BATCH_TITLES[index]:
            errors.append(f"{prefix}: title must match the Boardy source title at this position")
        resolved.append({"id": batch_id, "title": title, "order": order})
    return resolved


def _validate_scenes(
    panels: list[dict[str, Any]], batches: list[dict[str, Any]], errors: list[str],
) -> None:
    if len(panels) != 108:
        errors.append("Boardy production requires exactly 108 scene records")
    batch_ids = {batch["id"] for batch in batches if isinstance(batch["id"], str)}
    counts = dict.fromkeys(batch_ids, 0)
    seen_numbers: set[int] = set()
    for index, panel in enumerate(panels):
        number, batch_id = panel["scene_number"], panel["batch_id"]
        prefix = f"panel '{panel['name']}'"
        if not _positive_integer(number) or number > 108:
            errors.append(f"{prefix}: scene_number must be an integer in 1–108")
        else:
            if number in seen_numbers:
                errors.append(f"{prefix}: duplicate scene number {number}")
            seen_numbers.add(number)
            if number != index + 1:
                errors.append(f"{prefix}: scene order must increase from 1 through 108")
            expected_index = (number - 1) // 12
            if expected_index < len(batches) and batch_id != batches[expected_index]["id"]:
                errors.append(f"{prefix}: scene belongs to a different batch in batch order")
        if not isinstance(batch_id, str) or not batch_id.strip():
            errors.append(f"{prefix}: batch_id must be a non-empty string")
        elif batch_id not in batch_ids:
            errors.append(f"{prefix}: unknown batch ID '{batch_id}'")
        else:
            counts[batch_id] += 1
    if seen_numbers != set(range(1, 109)):
        errors.append("Boardy scene numbers must contain every number in 1–108 exactly once")
    for batch_id, count in counts.items():
        if count != 12:
            errors.append(f"batch '{batch_id}': requires exactly twelve scenes, got {count}")


def load_manifest(project_dir: Path | str) -> dict[str, Any]:
    """Load and validate production.json. Returns the parsed manifest, defaults filled in."""
    project = Path(project_dir).resolve()
    path = project / "production.json"
    if not path.is_file():
        raise CatalogError([f"Production manifest does not exist: {path}"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CatalogError([f"{path} is not valid JSON: {error}"]) from error

    errors: list[str] = []
    if not isinstance(data, dict) or data.get("version") not in (1, 2):
        raise CatalogError([f"{path}: unsupported or missing 'version' (expected 1 or 2)"])
    batched = data["version"] == 2
    if batched and type(data["version"]) is not int:
        errors.append("'version' must be an integer")
    canon_path = None
    batches = []
    if batched:
        try:
            canon = data.get("canon")
            if isinstance(canon, str) and not canon.strip():
                raise CatalogError(["canon must be a non-empty string"])
            canon_path = _resolve_project_path(project, canon, field="canon")
        except CatalogError as error:
            errors.extend(error.errors)
        batches = _load_batches(data, errors)
    panels = data.get("panels") if isinstance(data, dict) else None
    if not isinstance(panels, list) or not panels:
        raise CatalogError(errors + [f"{path}: 'panels' must be a non-empty array"])

    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        if batched:
            errors.append("'defaults' must be an object")
        defaults = {}
    resolved_defaults = {
        "width": defaults.get("width", 1024),
        "height": defaults.get("height", 1536),
        "max_render_references": defaults.get("max_render_references", DEFAULT_MAX_RENDER_REFERENCES),
    }
    if batched:
        for field, value in resolved_defaults.items():
            if not _positive_integer(value):
                errors.append(f"defaults.{field} must be a positive integer")

    seen_names: set[str] = set()
    resolved_panels = []
    for index, panel in enumerate(panels):
        prefix = f"panel[{index}]"
        if not isinstance(panel, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        name = panel.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{prefix}: 'name' must be a non-empty string")
            name = f"{prefix}"
        elif name in seen_names:
            errors.append(f"panel '{name}': duplicate panel name")
        seen_names.add(name)

        brief = panel.get("brief")
        try:
            brief_path = _resolve_project_path(project, brief, field=f"panel '{name}'.brief")
            if not brief_path.is_file():
                errors.append(f"panel '{name}': brief does not exist: {brief_path}")
            elif not brief_path.read_text(encoding="utf-8").strip():
                errors.append(f"panel '{name}': brief is empty: {brief_path}")
        except CatalogError as error:
            errors.extend(error.errors)
            brief_path = None

        references = panel.get("references")
        if not isinstance(references, list) or not references:
            errors.append(f"panel '{name}': 'references' must be a non-empty array of reference IDs")
            references = []
        elif batched and any(not isinstance(ref, str) or not ref.strip() for ref in references):
            errors.append(f"panel '{name}': reference IDs must be non-empty strings")
            references = []
        elif len(set(references)) != len(references):
            errors.append(f"panel '{name}': duplicate reference ID within one panel")

        seed = panel.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            errors.append(f"panel '{name}': 'seed' must be a non-negative integer")

        width = panel.get("width", resolved_defaults["width"])
        height = panel.get("height", resolved_defaults["height"])
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            errors.append(f"panel '{name}': width/height must be positive integers")
        elif batched and (isinstance(width, bool) or isinstance(height, bool)):
            errors.append(f"panel '{name}': width/height must be positive integers")

        max_refs = panel.get("max_render_references", resolved_defaults["max_render_references"])
        if batched and not _positive_integer(max_refs):
            errors.append(f"panel '{name}': max_render_references must be a positive integer")
        elif len(references) > max_refs:
            errors.append(
                f"panel '{name}': {len(references)} references exceeds the configured "
                f"maximum of {max_refs}"
            )

        resolved_panels.append(
            {
                "name": name,
                "brief": brief_path,
                "references": references,
                "seed": seed,
                "width": width,
                "height": height,
                "max_render_references": max_refs,
            }
        )
        if batched:
            resolved_panels[-1].update(
                scene_number=panel.get("scene_number"), batch_id=panel.get("batch_id"),
            )

    if batched:
        _validate_scenes(resolved_panels, batches, errors)

    if errors:
        raise CatalogError(errors)
    result = {"defaults": resolved_defaults, "panels": resolved_panels}
    if batched:
        result.update(canon=canon_path, batches=batches)
    return result


def resolve_panel_references(
    panel: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    *,
    allow_draft: bool = False,
) -> list[Path]:
    """Resolve a manifest panel's reference IDs into ordered, approved file paths."""
    errors: list[str] = []
    resolved: list[Path] = []
    for reference_id in panel["references"]:
        entry = catalog.get(reference_id)
        if entry is None:
            errors.append(f"panel '{panel['name']}': unknown reference ID '{reference_id}'")
            continue
        if entry["status"] != "approved" and not allow_draft:
            errors.append(
                f"panel '{panel['name']}': reference '{reference_id}' is not approved "
                f"(status: {entry['status']})"
            )
            continue
        if not entry["file"].is_file():
            errors.append(
                f"panel '{panel['name']}': reference '{reference_id}' has no asset file yet: {entry['file']}"
            )
            continue
        resolved.append(entry["file"])
    if errors:
        raise CatalogError(errors)
    return resolved
