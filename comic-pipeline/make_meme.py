"""One-command wrapper: meme brief + template -> directed, composited meme.

Chains meme_sources (template fetch/cache), direct_meme (Ollama direction),
optionally comfy_client (ComfyUI remix render), and meme_compose (caption
compositing) for a single project-relative meme. Mirrors make_panel.py's
shape and the sequential-GPU rule: Ollama direction always finishes (and
unloads) before any ComfyUI call.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import comfy_client
import direct_meme
import meme_compose
import meme_sources
import pipeline_config
from direct_panel import write_json_atomic


def _requests_path(project: Path, name: str) -> Path:
    return project / "requests" / f"{name}.json"


def _failure_path(project: Path, name: str) -> Path:
    return project / "requests" / f"{name}-failure.json"


def make_meme(
    project_dir: Path | str,
    name: str,
    *,
    brief_text: str,
    seed: int,
    template: dict[str, Any] | None = None,
    allow_remix: bool = True,
    model: str | None = None,
    ollama_url: str | None = None,
    comfy_url: str | None = None,
    comfy_output_root: Path | str | None = None,
    render_timeout: float = 180.0,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    if not brief_text.strip():
        raise ValueError("Meme brief cannot be empty")

    # Unset settings fall back to environment/config file; see pipeline_config.
    model = pipeline_config.model(model)
    ollama_url = pipeline_config.ollama_url(ollama_url)
    comfy_url = pipeline_config.comfy_url(comfy_url)

    project = Path(project_dir).resolve()
    output_dir = project / "outputs"
    subfolder = project.name

    try:
        log("[1/4] Selecting template...")
        if template is None:
            templates = meme_sources.fetch_top_templates()
            template = meme_sources.pick_template(templates, rng=random.Random(seed))
        template_path, template_hash = meme_sources.download_template(template, project / "templates")

        log(f"[2/4] Directing meme '{name}' via Ollama ({model})...")
        director = direct_meme.run_direction(
            template_path, template, brief_text, model=model, base_url=ollama_url
        )
        if not allow_remix:
            # Defense in depth: don't trust the model to always honor an
            # instruction-only constraint against remixing.
            director["remix"]["enabled"] = False

        record: dict[str, Any] = {
            "template": {**template, "local_path": str(template_path), "sha256": template_hash},
            **director,
            "seed": seed,
            "model": model,
        }
        write_json_atomic(_requests_path(project, name), record)

        caption_base_image = template_path
        if director["remix"]["enabled"]:
            log("[3/4] Checking ComfyUI and rendering remix...")
            try:
                comfy_client.health(comfy_url)
            except RuntimeError as error:
                raise RuntimeError(
                    f"{error} Start ComfyUI first (see README.md), then re-run this command."
                ) from error
            reference_name = comfy_client.upload_image(template_path, comfy_url, subfolder=subfolder)
            workflow = comfy_client.prepare_reference_edit(
                director["remix"]["positive_prompt"],
                reference_name,
                project / "requests" / f"{name}-workflow.json",
                seed=seed,
                filename_prefix=f"{subfolder}/{name}-remix",
                width=template["width"],
                height=template["height"],
            )
            prompt_id = comfy_client.submit(workflow, comfy_url)
            entry = comfy_client.wait_for_completion(prompt_id, comfy_url, timeout=render_timeout)
            images = comfy_client.output_images(entry)
            if not images:
                raise RuntimeError(f"ComfyUI finished prompt {prompt_id} but produced no images")
            output_root = pipeline_config.comfy_output_root(comfy_output_root)
            remix_image = images[0]
            source = output_root / remix_image.get("subfolder", "") / remix_image["filename"]
            caption_base_image = project / "requests" / f"{name}-remix.png"
            caption_base_image.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, caption_base_image)
        else:
            log("[3/4] Skipping remix (not requested).")

        log("[4/4] Compositing captions...")
        final_path = meme_compose.compose(
            caption_base_image,
            director["captions"],
            output_path=output_dir / f"{name}.png",
            box_count=template["box_count"],
        )
        record["output_path"] = str(final_path)
        write_json_atomic(_requests_path(project, name), record)
        _failure_path(project, name).unlink(missing_ok=True)  # clear a stale failure from an earlier attempt
        return record
    except Exception as error:
        write_json_atomic(_failure_path(project, name), {"name": name, "seed": seed, "error": str(error)})
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="Project directory (templates/, requests/, outputs/)")
    parser.add_argument("--name", required=True, help="Meme name; output is written to <project>/outputs/<name>.png")
    parser.add_argument("--brief", required=True, type=Path, help="Path to a text file with the meme topic/vibe brief")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--template-id", help="Imgflip source_id to use instead of a random pick")
    parser.add_argument("--no-remix", action="store_true", help="Never remix the base artwork, caption only")
    parser.add_argument("--model", help=f"Ollama director model (default: {pipeline_config.DEFAULT_MODEL})")
    parser.add_argument("--ollama-url", help=f"default: {pipeline_config.DEFAULT_OLLAMA_URL}")
    parser.add_argument("--comfy-url", help=f"default: {pipeline_config.DEFAULT_COMFY_URL}")
    parser.add_argument("--comfy-output-root", type=Path, help="Where ComfyUI writes images (default: <comfy_root>/output)")
    parser.add_argument("--render-timeout", type=float, default=180.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    brief_text = args.brief.read_text(encoding="utf-8")
    template = None
    if args.template_id:
        templates = meme_sources.fetch_top_templates()
        template = meme_sources.pick_template(templates, source_id=args.template_id)
    try:
        record = make_meme(
            args.project,
            args.name,
            brief_text=brief_text,
            seed=args.seed,
            template=template,
            allow_remix=not args.no_remix,
            model=args.model,
            ollama_url=args.ollama_url,
            comfy_url=args.comfy_url,
            comfy_output_root=args.comfy_output_root,
            render_timeout=args.render_timeout,
            log=lambda message: print(message, file=sys.stderr),
        )
    except (ValueError, RuntimeError, TimeoutError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(record["output_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
