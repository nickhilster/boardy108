"""Schedule Boardy scenes sequentially, isolating individual render failures."""

from __future__ import annotations

import uuid
import time
import json
import argparse
import sys
from pathlib import Path
from typing import Any, Callable

import canon
import make_panel
import preflight_project
import reference_catalog
import render_project
import production_review


def _status(scenes: list[dict[str, Any]]) -> str:
    states = {scene["status"] for scene in scenes}
    if states <= {"complete", "skipped"}:
        return "complete"
    if states == {"pending"}:
        return "pending"
    return "partial" if states & {"complete", "skipped"} else "failed"


def run_batches(
    project_dir: Path | str,
    *,
    batch_id: str | None = None,
    scene_name: str | None = None,
    resume: bool = False,
    dry_run: bool = False,
    make_panel_fn: Callable[..., list[Path]] = make_panel.make_panel,
    log: Callable[[str], None] = print,
    **make_panel_kwargs: Any,
) -> dict[str, Any]:
    project = Path(project_dir).resolve()
    preflight = preflight_project.run_preflight(project)
    if not preflight["ok"]:
        raise ValueError("Boardy preflight failed: " + "; ".join(preflight["errors"]))
    manifest = reference_catalog.load_manifest(project)
    if "batches" not in manifest:
        raise ValueError("Batch execution requires a version-2 Boardy manifest")
    if scene_name is not None:
        panel = next((panel for panel in manifest["panels"] if panel["name"] == scene_name), None)
        if panel is None:
            raise ValueError(f"Scene not found: {scene_name}")
        if batch_id is not None and batch_id != panel["batch_id"]:
            raise ValueError(f"Scene {scene_name} does not belong to batch {batch_id}")
        batch_id = panel["batch_id"]
        manifest = {**manifest, "panels": [panel]}
    batches = [batch for batch in manifest["batches"] if batch_id is None or batch["id"] == batch_id]
    if not batches:
        raise ValueError(f"Batch not found: {batch_id}")
    catalog = reference_catalog.load_catalog(project)
    canon_instructions = canon.format_instructions(canon.load_canon(project))
    selected_panels = {}
    selected_references = {}
    # Validate revision integrity and hash/config resolution before opening a run.
    for panel in manifest["panels"]:
        if panel["batch_id"] in {batch["id"] for batch in batches}:
            selected_panels[panel["name"]] = production_review.active_panel(project, panel)
            selected_references[panel["name"]] = reference_catalog.resolve_panel_references(panel, catalog)
            render_project._build_expected_metadata(project,
                {**selected_panels[panel["name"]], "_resolved_references": selected_references[panel["name"]]}, make_panel_kwargs)
    result: dict[str, Any] = {"run_id": uuid.uuid4().hex, "batches": [], "scenes": [],
        "started_at": production_review.utc_now(), "finished_at": None,
        "elapsed_seconds": 0.0, "comparison_seconds": 355, "review_status": "pending",
        "render_status": "pending" if dry_run else "running", "failures": []}
    started = time.monotonic()
    run_path = project / "runs" / f"{result['run_id']}.json"
    def checkpoint():
        result["elapsed_seconds"] = time.monotonic() - started
        if not dry_run:
            production_review.write_atomic(run_path, result)
    for batch in batches:
        scenes = [{"name": panel["name"], "scene_number": panel["scene_number"],
                   "batch_id": batch["id"], "status": "pending", "render_status": "pending",
                   "review_status": "pending", "attempt": None}
                  for panel in manifest["panels"] if panel["batch_id"] == batch["id"]]
        result["scenes"].extend(scenes)
        result["batches"].append({"id": batch["id"], "title": batch["title"],
                                  "status": "pending", "render_status": "pending", "scenes": scenes})
    checkpoint()
    log(f"Run ID: {result['run_id']}")
    try:
        for batch in result["batches"]:
            batch["render_status"] = "running" if not dry_run else "pending"
            for original_panel in manifest["panels"]:
                if original_panel["batch_id"] != batch["id"]:
                    continue
                scene = next(item for item in batch["scenes"] if item["name"] == original_panel["name"])
                panel = selected_panels[scene["name"]]
                references = selected_references[scene["name"]]
                stage = "resume" if resume else "render"
                try:
                    if dry_run:
                        log(f"[dry-run] {batch['id']}: {panel['name']}")
                    elif resume and render_project._can_resume(project, {**panel, "_resolved_references": references}, make_panel_kwargs):
                        scene["status"] = "skipped"
                        scene["render_status"] = "complete"
                        scene["metadata"] = json.loads(render_project._metadata_path(project, panel["name"]).read_text(encoding="utf-8"))
                        scene["attempt"] = scene["metadata"]["attempt"]
                        scene["review_status"] = production_review.review_status(project, panel["name"], scene["metadata"]["output_hash"])
                        log(f"[skip] {panel['name']}")
                    else:
                        stage = "render"
                        log(f"=== {panel['name']} ===")
                        scene["render_status"] = "running"
                        checkpoint()
                        scene["metadata"] = render_project.render_one_panel(
                            project, panel, references, make_panel_fn=make_panel_fn,
                            canon_instructions=canon_instructions, output_subdir=batch["id"],
                            run_id=result["run_id"], log=log, **make_panel_kwargs,
                        )
                        scene["status"] = "complete"
                        scene["render_status"] = "complete"
                        scene["review_status"] = scene["metadata"]["review_status"]
                        scene["attempt"] = scene["metadata"]["attempt"]
                except Exception as error:
                    scene.update(status="failed", render_status="failed", error=str(error))
                    failure_path = render_project._failure_path(project, panel["name"])
                    if stage == "resume" or not failure_path.is_file():
                        production_review.write_atomic(failure_path, {
                            "name": panel["name"], "scene_id": panel["scene_number"],
                            "batch_id": batch["id"], "run_id": result["run_id"],
                            "stage": stage, "error": str(error), "render_status": "failed",
                            "finished_at": production_review.utc_now(),
                        })
                    scene["failure"] = json.loads(failure_path.read_text(encoding="utf-8"))
                    scene["attempt"] = scene["failure"].get("attempt")
                    result["failures"].append({"name": panel["name"], "stage": stage, "error": str(error)})
                    log(f"[failed] {panel['name']}: {error}")
                batch["status"] = _status(batch["scenes"])
                checkpoint()
            batch["render_status"] = batch["status"] = _status(batch["scenes"])
            batch["review_status"] = _review_status(batch["scenes"])
            checkpoint()
    except BaseException as error:
        result["failures"].append({"stage": "run", "error": str(error), "type": type(error).__name__})
        for scene in result["scenes"]:
            if scene["render_status"] in {"pending", "running"}:
                scene.update(status="failed", render_status="failed", error=f"Run terminated: {error}")
        raise
    finally:
        for batch in result["batches"]:
            batch["render_status"] = batch["status"] = _status(batch["scenes"])
            batch["review_status"] = _review_status(batch["scenes"])
        result["status"] = _status(result["scenes"])
        result["render_status"] = result["status"]
        result["review_status"] = _review_status(result["scenes"])
        result["finished_at"] = production_review.utc_now()
        checkpoint()
    return result


def _review_status(scenes: list[dict[str, Any]]) -> str:
    states = {scene["review_status"] for scene in scenes}
    if states == {"accepted"}:
        return "accepted"
    if "review-needed" in states:
        return "review-needed"
    if "rejected" in states:
        return "rejected"
    return "pending"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--batch", help="One manifest batch ID; default: all nine batches")
    parser.add_argument("--scene", help="One manifest scene name for a smoke render")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list scenes without services or artifacts")
    parser.add_argument("--model", help="Local Ollama director model")
    parser.add_argument("--ollama-url")
    parser.add_argument("--director", choices=("local", "cloud"), default="local")
    parser.add_argument("--cloud-provider")
    parser.add_argument("--cloud-model")
    parser.add_argument("--comfy-url")
    parser.add_argument("--comfy-output-root", type=Path)
    parser.add_argument("--render-timeout", type=float, default=180.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_batches(args.project, batch_id=args.batch, scene_name=args.scene,
                             resume=args.resume, dry_run=args.dry_run,
                             model=args.model, ollama_url=args.ollama_url,
                             director=args.director, cloud_provider=args.cloud_provider,
                             cloud_model=args.cloud_model,
                             comfy_url=args.comfy_url, comfy_output_root=args.comfy_output_root,
                             render_timeout=args.render_timeout,
                             log=lambda message: print(message, file=sys.stderr, flush=True))
    except (ValueError, RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

