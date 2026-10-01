"""Human decisions and immutable brief revisions, bound to a scene's output."""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import reference_catalog


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def scene_panel(project: Path, name: str) -> dict[str, Any]:
    for panel in reference_catalog.load_manifest(project)["panels"]:
        if panel["name"] == name:
            return panel
    raise ValueError(f"Unknown scene: {name}")


def revision_history(project: Path, name: str) -> list[dict[str, Any]]:
    path = project / "revisions" / name / "history.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def active_panel(project: Path, panel: dict[str, Any]) -> dict[str, Any]:
    history = revision_history(project, panel["name"])
    if history:
        latest = history[-1]
        source = project / latest["brief_path"]
        import render_project
        if not source.is_file() or render_project._sha256_file(source) != latest["brief_hash"]:
            raise ValueError(f"Brief revision was changed: {panel['name']}")
        return {**panel, "brief": source, "brief_revision": latest["revision"]}
    return {**panel, "brief_revision": 0}


def current_output(project: Path, name: str) -> dict[str, Any]:
    import render_project
    scene_panel(project, name)
    path = render_project._metadata_path(project, name)
    if not path.is_file():
        raise ValueError(f"No completed output for scene: {name}")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    outputs = metadata.get("outputs", [])
    if len(outputs) != 1 or not Path(outputs[0]).is_file():
        raise ValueError(f"Missing completed output for scene: {name}")
    if render_project._sha256_file(Path(outputs[0])) != metadata.get("output_hash"):
        metadata["review_status"] = "pending"
        write_atomic(path, metadata)
        raise ValueError(f"Stale output hash for scene: {name}")
    return metadata


def review_status(project: Path, name: str, output_hash: str | None) -> str:
    path = project / "reviews" / f"{name}.json"
    if not path.is_file():
        return "pending"
    history = json.loads(path.read_text(encoding="utf-8"))
    latest = history[-1]
    return latest["status"] if latest["output_hash"] == output_hash else "pending"


def record_review(project_dir: Path | str, scene_name: str, status: str, note: str = "",
                  *, reviewer: str | None = None, expected_output_hash: str | None = None) -> dict[str, Any]:
    if status not in {"accepted", "rejected", "review-needed"}:
        raise ValueError(f"Unsupported review status: {status}")
    project = Path(project_dir).resolve()
    metadata = current_output(project, scene_name)
    if expected_output_hash is not None and expected_output_hash != metadata["output_hash"]:
        raise ValueError("Stale output hash supplied for review")
    record = {"event_id": uuid.uuid4().hex, "scene_id": metadata["scene_number"],
              "name": scene_name, "status": status, "note": note, "reviewer": reviewer,
              "timestamp": utc_now(), "output_hash": metadata["output_hash"]}
    path = project / "reviews" / f"{scene_name}.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    write_atomic(path, history + [record])
    metadata["review_status"] = status
    import render_project
    write_atomic(render_project._metadata_path(project, scene_name), metadata)
    return record


def record_brief_revision(project_dir: Path | str, scene_name: str, revision_path: Path | str) -> dict[str, Any]:
    import render_project
    project = Path(project_dir).resolve()
    panel = scene_panel(project, scene_name)
    source = Path(revision_path).resolve()
    if not source.is_file() or not source.read_text(encoding="utf-8").strip():
        raise ValueError("Revision must be an existing nonempty UTF-8 brief")
    history = revision_history(project, scene_name)
    revision = len(history) + 1
    folder = project / "revisions" / scene_name
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"brief-{revision:03d}.txt"
    if destination.exists():
        raise ValueError("Revision destination already exists")
    shutil.copyfile(source, destination)
    record = {"scene_id": panel["scene_number"], "name": scene_name, "revision": revision,
              "original_brief_path": str(panel["brief"].relative_to(project)),
              "original_brief_hash": render_project._sha256_file(panel["brief"]),
              "brief_path": str(destination.relative_to(project)),
              "brief_hash": render_project._sha256_file(destination),
              "previous_brief_hash": history[-1]["brief_hash"] if history else render_project._sha256_file(panel["brief"]),
              "timestamp": utc_now()}
    write_atomic(folder / "history.json", history + [record])
    return record
