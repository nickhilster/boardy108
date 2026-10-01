"""Render an entire production manifest sequentially, one panel at a time.

Delegates each panel to make_panel.make_panel (the existing single-panel
orchestration, unchanged), so Ollama direction and ComfyUI sampling are still
never run concurrently. Writes a production-metadata sidecar per completed
panel for safe, hash-based resume, and stops -- rather than skipping ahead --
on the first failure.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import inspect
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import comfy_supervisor
import make_panel
import pipeline_config
import reference_catalog
import production_review


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metadata_path(project: Path, name: str) -> Path:
    return project / "requests" / f"{name}-production.json"


def _failure_path(project: Path, name: str) -> Path:
    return project / "requests" / f"{name}-failure.json"


def _hash_json(value: Any) -> str:
    return _sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _artifact_inventory(project: Path) -> dict[Path, str]:
    return {path: _sha256_file(path) for folder in (project / "requests", project / "outputs")
            for path in folder.rglob("*") if path.is_file()}


def _archive_failed_artifacts(project: Path, attempt_dir: Path, before: dict[Path, str],
                              name: str) -> list[dict[str, str]]:
    artifacts = []
    for path, digest in _artifact_inventory(project).items():
        if before.get(path) == digest and not path.name.startswith(name):
            continue
        relative = path.relative_to(project)
        destination = attempt_dir / "failed-artifacts" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        artifacts.append({"source_path": str(relative), "archive_path": str(destination.relative_to(project)),
                          "hash": digest})
    return artifacts


def _settings_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.resolve())
    if callable(value):
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):
            source = ""
        return {"callable": getattr(value, "__qualname__", type(value).__qualname__),
                "module": getattr(value, "__module__", ""), "source_hash": _sha256_text(source)}
    return value


def _build_expected_metadata(project: Path, panel: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {
        "name": panel["name"],
        "seed": panel["seed"],
        "width": panel["width"],
        "height": panel["height"],
        "brief_hash": _sha256_file(panel["brief"]),
        "references": [
            {"id": reference_id, "hash": _sha256_file(panel["_resolved_references"][index])}
            for index, reference_id in enumerate(panel["references"])
        ],
    }
    if "batch_id" in panel:
        options = options or {}
        director = options.get("director", "local")
        model_settings = {"director": director,
            "model": pipeline_config.model(options.get("model")) if director == "local" else pipeline_config.cloud_director_model(options.get("cloud_model")),
            "provider": "ollama" if director == "local" else pipeline_config.cloud_director_provider(options.get("cloud_provider")),
            "endpoint": pipeline_config.ollama_url(options.get("ollama_url")) if director == "local" else None,
            "director_source_hash": _sha256_file(Path(__file__).with_name("direct_panel.py" if director == "local" else "cloud_director.py")),
            "schema_hash": _sha256_file(Path(__file__).with_name("panel_schema.json"))}
        if model_settings["provider"] == "session":
            model_settings["direction_hash"] = _sha256_file(project / "directions" / f"{panel['name']}.json")
        model_settings["canon_formatter_hash"] =_sha256_file(Path(__file__).with_name("canon.py"))
        render_settings = {key: _settings_value(value) for key, value in options.items()
                           if key not in {"log", "canon_instructions", "output_subdir", "brief_path"}}
        render_settings.update(max_render_references=options.get("max_render_references", panel["max_render_references"]),
                               comfy_url=pipeline_config.comfy_url(options.get("comfy_url")),
                               render_timeout=options.get("render_timeout", 180.0),
                               workflow_source_hash=_sha256_file(Path(__file__).with_name("comfy_client.py")),
                               orchestration_source_hash=_sha256_file(Path(__file__).with_name("make_panel.py")))
        if options.get("render_reference") is not None:
            render_settings["promoted_reference_hash"] = _sha256_file(Path(options["render_reference"]))
        metadata.update(canon_hash=_sha256_file(project / "canon.json"), canon_version=1,
                        brief_revision=panel.get("brief_revision", 0),
                        model_settings=model_settings, model_hash=_hash_json(model_settings),
                        render_settings=render_settings, render_hash=_hash_json(render_settings))
        metadata["input_hash"] = _hash_json(metadata)
    return metadata


def _can_resume(project: Path, panel: dict[str, Any], options: dict[str, Any] | None = None) -> bool:
    metadata_path = _metadata_path(project, panel["name"])
    if not metadata_path.is_file():
        return False
    try:
        saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    expected = _build_expected_metadata(project, panel, options)
    if "batch_id" in panel:
        try:
            output = Path(saved["outputs"][0])
            request = project / "requests" / f"{panel['name']}.json"
            workflow = project / "requests" / f"{panel['name']}-workflow.json"
            return (saved.get("render_status") == "complete" and saved.get("input_hash") == expected["input_hash"]
                    and len(saved["outputs"]) == 1 and output.is_file()
                    and output.parent == (project / "outputs" / panel["batch_id"]).resolve()
                    and output.stem == panel["name"] and _sha256_file(output) == saved.get("output_hash")
                    and (not saved.get("request_hash") or (request.is_file() and _sha256_file(request) == saved["request_hash"]))
                    and (not saved.get("workflow_hash") or (workflow.is_file() and _sha256_file(workflow) == saved["workflow_hash"])))
        except (KeyError, IndexError, OSError, TypeError):
            return False
    return saved.get("seed") == expected["seed"] and saved.get("brief_hash") == expected["brief_hash"] and saved.get(
        "references"
    ) == expected["references"] and saved.get("width") == expected["width"] and saved.get("height") == expected["height"]


def render_one_panel(
    project: Path | str,
    panel: dict[str, Any],
    resolved_references: list[Path],
    *,
    make_panel_fn: Callable[..., list[Path]] = make_panel.make_panel,
    canon_instructions: str | None = None,
    output_subdir: str | None = None,
    run_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Render a single item; Boardy items require one existing scene output."""
    project = Path(project).resolve()
    panel = {**panel, "_resolved_references": resolved_references}
    options = dict(seed=panel["seed"], width=panel["width"], height=panel["height"],
                   max_render_references=panel["max_render_references"])
    boardy = "batch_id" in panel
    started_at = production_review.utc_now()
    started = time.monotonic()
    attempt_dir = None
    artifact_inventory = {}
    stage_events = [{"stage": "scene-start", "timestamp": started_at}]
    options.update(kwargs)
    if canon_instructions is not None:
        options["canon_instructions"] = canon_instructions
    if output_subdir is not None:
        options["output_subdir"] = output_subdir
    if boardy:
        options["brief_path"] = panel["brief"]
        original_log = options.get("log", print)
        def stage_log(message: str) -> None:
            if message.startswith(("[1/4]", "[2/4]", "[3/4]", "[4/4]", "[recover]")):
                stage_events.append({"stage": message, "timestamp": production_review.utc_now()})
            original_log(message)
        options["log"] = stage_log
    try:
        if boardy:
            for setting in ("seed", "width", "height"):
                if setting in kwargs and kwargs[setting] != panel[setting]:
                    raise ValueError(f"Boardy {setting} must match the production manifest")
            expected = _build_expected_metadata(project, panel, kwargs)
            attempts = project / "attempts" / panel["name"]
            attempt = len(list(attempts.glob("*/attempt.json"))) + 1
            attempt_dir = attempts / f"{attempt:04d}-{uuid.uuid4().hex}"
            attempt_dir.mkdir(parents=True)
            # Snapshot all prior artifacts before the renderer can overwrite paths.
            previous = attempt_dir / "previous"
            for path in [*_metadata_path(project, panel["name"]).parent.glob(f"{panel['name']}*.json"),
                         *(project / "outputs" / panel["batch_id"]).glob(f"{panel['name']}.*")]:
                previous.mkdir(exist_ok=True)
                shutil.copyfile(path, previous / path.name)
            shutil.copyfile(panel["brief"], attempt_dir / "brief.txt")
            shutil.copyfile(project / "canon.json", attempt_dir / "canon.json")
            for index, reference in enumerate(resolved_references):
                shutil.copyfile(reference, attempt_dir / f"reference-{index}{reference.suffix}")
            production_review.write_atomic(attempt_dir / "attempt.json", {**expected, "attempt": attempt,
                "started_at": started_at, "render_status": "running"})
            artifact_inventory = _artifact_inventory(project)
        outputs = make_panel_fn(project, panel["name"], resolved_references, **options)
        if boardy:
            if _build_expected_metadata(project, panel, kwargs)["input_hash"] != expected["input_hash"]:
                raise ValueError("Production inputs changed during rendering; output retained for review")
            if len(outputs) != 1 or not Path(outputs[0]).is_file():
                raise ValueError("Boardy scene must produce exactly one existing image file")
            output = Path(outputs[0]).resolve()
            expected_dir = (project / "outputs" / panel["batch_id"]).resolve()
            if output.parent != expected_dir or output.stem != panel["name"]:
                raise ValueError("Boardy image must belong to its scene name and batch output directory")
        metadata = expected if boardy else _build_expected_metadata(project, panel)
        if boardy:
            metadata.update(scene_number=panel["scene_number"], batch_id=panel["batch_id"],
                            scene_id=panel["scene_number"], run_id=run_id,
                            outputs=[str(Path(output).resolve()) for output in outputs],
                            output_hash=_sha256_file(output), render_status="complete", attempt=attempt,
                            attempt_id=attempt_dir.name, started_at=started_at,
                            finished_at=production_review.utc_now(), elapsed_seconds=time.monotonic() - started,
                            stage_events=stage_events)
            metadata["review_status"] = production_review.review_status(project, panel["name"], metadata["output_hash"])
            for kind, filename in (("request", f"{panel['name']}.json"), ("workflow", f"{panel['name']}-workflow.json")):
                path = project / "requests" / filename
                metadata[f"{kind}_hash"] = _sha256_file(path) if path.is_file() else None
                if path.is_file():
                    shutil.copyfile(path, attempt_dir / filename)
            shutil.copyfile(output, attempt_dir / output.name)
            production_review.write_atomic(attempt_dir / "attempt.json", metadata)
        metadata_path = _metadata_path(project, panel["name"])
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        production_review.write_atomic(metadata_path, metadata)
        _failure_path(project, panel["name"]).unlink(missing_ok=True)
    except BaseException as error:
        failure_path = _failure_path(project, panel["name"])
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure = {"name": panel["name"], "stage": "make_panel", "error": str(error)}
        if boardy:
            failure.update(scene_number=panel["scene_number"], batch_id=panel["batch_id"])
            failure.update(scene_id=panel["scene_number"], run_id=run_id)
            failure.update(started_at=started_at, finished_at=production_review.utc_now(),
                           elapsed_seconds=time.monotonic() - started, render_status="failed", stage_events=stage_events)
            if attempt_dir is not None:
                failure.update(expected, attempt=attempt, attempt_id=attempt_dir.name)
                failure["artifacts"] = _archive_failed_artifacts(project, attempt_dir, artifact_inventory, panel["name"])
                production_review.write_atomic(attempt_dir / "attempt.json", failure)
                _metadata_path(project, panel["name"]).unlink(missing_ok=True)
        production_review.write_atomic(failure_path, failure)
        if not isinstance(error, Exception):
            raise
        raise RuntimeError(f"panel '{panel['name']}' failed: {error}") from error
    return metadata


def render_project(
    project_dir: Path | str,
    *,
    start_at: str | None = None,
    stop_after: str | None = None,
    resume: bool = False,
    dry_run: bool = False,
    allow_draft_references: bool = False,
    max_render_references: int | None = None,
    make_panel_fn: Callable[..., list[Path]] = make_panel.make_panel,
    log: Callable[[str], None] = print,
    **make_panel_kwargs: Any,
) -> list[str]:
    """Render (or dry-run) every panel in production.json, in order.

    Returns the list of panel names actually rendered (empty for a dry run).
    Raises RuntimeError, stopping immediately, on the first panel failure.
    """
    project = Path(project_dir).resolve()
    catalog = reference_catalog.load_catalog(project)
    manifest = reference_catalog.load_manifest(project)
    panels = manifest["panels"]

    names = [panel["name"] for panel in panels]
    if start_at is not None and start_at not in names:
        raise ValueError(f"--start-at panel not found in manifest: {start_at}")
    if stop_after is not None and stop_after not in names:
        raise ValueError(f"--stop-after panel not found in manifest: {stop_after}")

    start_index = names.index(start_at) if start_at else 0
    stop_index = names.index(stop_after) if stop_after else len(panels) - 1
    selected = panels[start_index : stop_index + 1]

    rendered: list[str] = []
    for panel in selected:
        resolved_references = reference_catalog.resolve_panel_references(
            panel, catalog, allow_draft=allow_draft_references
        )
        panel = {**panel, "_resolved_references": resolved_references}
        panel_max_refs = max_render_references or panel["max_render_references"]

        if dry_run:
            log(
                f"[dry-run] {panel['name']}: seed={panel['seed']} "
                f"{panel['width']}x{panel['height']} refs={panel['references']}"
            )
            continue

        if resume and _can_resume(project, panel):
            log(f"[skip] {panel['name']}: already rendered and matches the manifest (resume)")
            continue

        log(f"=== {panel['name']} ===")
        render_one_panel(
            project, panel, resolved_references, make_panel_fn=make_panel_fn,
            max_render_references=panel_max_refs, log=log, **make_panel_kwargs,
        )
        rendered.append(panel["name"])

    return rendered


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--start-at")
    parser.add_argument("--stop-after")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-draft-references", action="store_true")
    parser.add_argument("--max-render-references", type=int)
    parser.add_argument("--director", choices=("local", "cloud"), default="local")
    parser.add_argument("--model")
    parser.add_argument("--ollama-url")
    parser.add_argument("--cloud-provider")
    parser.add_argument("--cloud-model")
    parser.add_argument("--comfy-url")
    parser.add_argument("--comfy-output-root", type=Path)
    parser.add_argument("--render-timeout", type=float, default=180.0)
    parser.add_argument(
        "--no-auto-recover",
        action="store_true",
        help="Disable the automatic ComfyUI restart-and-retry on failure; fail immediately instead",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    comfy_url = pipeline_config.comfy_url(args.comfy_url)
    recover = None if args.no_auto_recover else functools.partial(comfy_supervisor.restart_comfyui, comfy_url)
    try:
        rendered = render_project(
            args.project,
            start_at=args.start_at,
            stop_after=args.stop_after,
            resume=args.resume,
            dry_run=args.dry_run,
            allow_draft_references=args.allow_draft_references,
            max_render_references=args.max_render_references,
            director=args.director,
            model=args.model,
            ollama_url=args.ollama_url,
            cloud_provider=args.cloud_provider,
            cloud_model=args.cloud_model,
            comfy_url=comfy_url,
            comfy_output_root=args.comfy_output_root,
            render_timeout=args.render_timeout,
            recover=recover,
            log=lambda message: print(message, file=sys.stderr),
        )
    except (ValueError, RuntimeError, reference_catalog.CatalogError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"rendered": rendered}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
