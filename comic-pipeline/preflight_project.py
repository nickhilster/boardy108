"""Validate a production project before spending any Ollama/ComfyUI/GPU time.

Checks the reference catalog and production manifest together, resolves every
panel's references, and reports every problem found in one run rather than
stopping at the first one. Never contacts a local service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import reference_catalog
import canon
from PIL import Image


def _boardy_reference_errors(catalog: dict[str, dict[str, Any]]) -> list[str]:
    entry = catalog.get("boardy")
    if entry is None:
        return ["Boardy production requires reference ID 'boardy'"]
    errors = []
    if entry["kind"] != "character":
        errors.append("reference 'boardy': kind must be character")
    if entry["status"] != "approved":
        errors.append("reference 'boardy': not approved; Boardy production cannot use draft references")
    path = entry["file"]
    if not path.is_file():
        errors.append(f"reference 'boardy': image file does not exist: {path}")
        return errors
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        errors.append("reference 'boardy': image filename must end in .png, .jpg, .jpeg, or .webp")
    digest = entry.get("sha256")
    try:
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        errors.append(f"reference 'boardy': cannot read image file: {error}")
        return errors
    if not isinstance(digest, str) or digest != actual_digest:
        errors.append("reference 'boardy': sha256 must match the approved image bytes")
    try:
        with Image.open(path) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                errors.append("reference 'boardy': image must be PNG, JPEG, or WebP")
            image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        errors.append(f"reference 'boardy': invalid supported image: {error}")
    return errors


def run_preflight(project_dir: Path | str, *, allow_draft_references: bool = False) -> dict[str, Any]:
    project = Path(project_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    # Detect the version separately so a malformed manifest cannot hide an
    # independent canon problem from the aggregated input report.
    batched = False
    raw_manifest: dict[str, Any] = {}
    try:
        raw = json.loads((project / "production.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw_manifest = raw
            batched = raw.get("version") == 2
    except (OSError, ValueError):
        pass
    canon_report: dict[str, Any] = {"ok": False}
    if batched:
        if raw_manifest.get("canon") != "canon.json":
            errors.append("Boardy production must use project canon.json")
        try:
            canon_value = canon.load_canon(project)
            canon_report.update(ok=True, version=canon_value["version"], sha256=canon_value["sha256"])
        except (OSError, ValueError) as error:
            errors.append(f"canon.json: {error}")

    catalog: dict[str, dict[str, Any]] = {}
    try:
        catalog = reference_catalog.load_catalog(project)
    except reference_catalog.CatalogError as error:
        errors.extend(error.errors)

    if batched:
        errors.extend(_boardy_reference_errors(catalog))

    manifest: dict[str, Any] | None = None
    try:
        manifest = reference_catalog.load_manifest(project)
    except reference_catalog.CatalogError as error:
        errors.extend(error.errors)

    used_reference_ids: set[str] = set()
    panel_reports: list[dict[str, Any]] = []
    output_names_seen: dict[str, str] = {}

    if manifest is not None:
        for panel in manifest["panels"]:
            used_reference_ids.update(panel["references"])
            resolved_ok = True
            if batched and "boardy" not in panel["references"]:
                errors.append(f"panel '{panel['name']}': must reference canonical ID 'boardy'")
                resolved_ok = False
            try:
                reference_catalog.resolve_panel_references(
                    panel, catalog, allow_draft=allow_draft_references and not batched,
                )
            except reference_catalog.CatalogError as error:
                errors.extend(error.errors)
                resolved_ok = False

            metadata_path = project / "requests" / f"{panel['name']}-production.json"
            request_path = project / "requests" / f"{panel['name']}.json"
            has_completed_output = metadata_path.is_file()

            existing = output_names_seen.get(panel["name"])
            if existing:
                errors.append(f"panel '{panel['name']}': duplicate output name collides with {existing}")
            output_names_seen[panel["name"]] = panel["name"]

            if request_path.is_file() and not metadata_path.is_file():
                warnings.append(
                    f"panel '{panel['name']}': a request/output exists without production metadata "
                    "(not resumable; will be treated as not-yet-rendered)"
                )

            panel_reports.append(
                {
                    "name": panel["name"],
                    "references_ok": resolved_ok,
                    "completed": has_completed_output,
                }
            )

    for reference_id, entry in catalog.items():
        if reference_id not in used_reference_ids:
            warnings.append(f"reference '{reference_id}' is not used by any panel in production.json")
        if entry.get("status") == "draft":
            suffix = "" if allow_draft_references else " and will block production"
            warnings.append(f"reference '{reference_id}' is still draft{suffix}")

    report = {
        "project": str(project),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "panels": panel_reports,
    }
    if batched:
        report["canon"] = canon_report
        report["batches"] = [
            {"id": batch["id"], "title": batch["title"], "expected_count": 12,
             "scenes": [panel["name"] for panel in manifest["panels"]
                        if panel["batch_id"] == batch["id"]]}
            for batch in manifest["batches"]
        ] if manifest is not None else []
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument(
        "--allow-draft-references",
        action="store_true",
        help="Development only: do not block on draft/unapproved references",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_preflight(args.project, allow_draft_references=args.allow_draft_references)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
