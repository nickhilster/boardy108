"""One-command wrapper: panel brief + references -> directed, rendered frame.

Chains direct_panel (Ollama/Qwen3-VL direction) and comfy_client (ComfyUI/
FLUX.2 Klein render) for a single project-relative panel. Ollama and ComfyUI
are still used sequentially, in that order, matching the project's GPU rule;
this script does not start ComfyUI itself for a fresh run, since ComfyUI is
meant to stay resident across many panels (see start-comfyui.ps1 / README.md)
-- but if a render fails (ComfyUI unreachable or a render that never
completes, both symptoms of ComfyUI's known VRAM leak), it automatically
restarts ComfyUI once and retries, via comfy_supervisor. Pass
--no-auto-recover to disable that and fail immediately instead.

The direction stage itself is pluggable: --director local (default) uses
Ollama/Qwen3-VL, exactly as above; --director cloud uses cloud_director.py
instead -- a hosted model, provider selected by name (--cloud-provider,
default anthropic) rather than hardcoded, so a panel can be directed by
whichever model actually gets the brief right, at the cost of leaving the
local/free/GPU-only path.
"""

from __future__ import annotations

import argparse
import functools
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import cloud_director
import comfy_client
import comfy_supervisor
import direct_panel
import pipeline_config

DirectFn = Callable[..., dict[str, Any]]


def make_panel(
    project_dir: Path | str,
    name: str,
    references: list[Path | str],
    *,
    seed: int,
    brief_path: Path | str | None = None,
    render_reference: Path | str | None = None,
    director: str = "local",
    model: str | None = None,
    ollama_url: str | None = None,
    cloud_provider: str | None = None,
    cloud_model: str | None = None,
    direct_fn: DirectFn | None = None,
    comfy_url: str | None = None,
    comfy_output_root: Path | str | None = None,
    render_timeout: float = 180.0,
    width: int | None = None,
    height: int | None = None,
    max_render_references: int = comfy_client.DEFAULT_MAX_RENDER_REFERENCES,
    recover: Callable[[], None] | None = None,
    log=print,
    canon_instructions: str | None = None,
    output_subdir: str | None = None,
) -> list[Path]:
    # Unset settings fall back to environment/config file; see pipeline_config.
    # comfy_output_root stays unresolved until the render succeeds, so a run
    # that fails earlier reports that failure rather than a config complaint.
    comfy_url = pipeline_config.comfy_url(comfy_url)

    project = Path(project_dir).resolve()
    brief = Path(brief_path) if brief_path else project / "briefs" / f"{name}.txt"
    panel_json = project / "requests" / f"{name}.json"
    workflow_json = project / "requests" / f"{name}-workflow.json"
    output_dir = project / "outputs"
    if output_subdir is not None:
        if Path(output_subdir).is_absolute() or (output_dir / output_subdir).resolve().parent != output_dir.resolve():
            raise ValueError("output_subdir must be a single project output directory name")
        output_dir = output_dir / output_subdir
    subfolder = project.name

    if director not in ("local", "cloud"):
        raise ValueError(f"Unknown director: {director!r} (expected 'local' or 'cloud')")

    if director == "cloud":
        cloud_provider = pipeline_config.cloud_director_provider(cloud_provider)
        cloud_model = pipeline_config.cloud_director_model(cloud_model)
        log(f"[1/4] Directing panel '{name}' via cloud model ({cloud_provider}/{cloud_model})...")
        direct_fn = direct_fn or cloud_director.run_direction
        direction_options = dict(provider=cloud_provider, model=cloud_model)
        if canon_instructions is not None:
            direction_options["canon_instructions"] = canon_instructions
        direct_fn(brief, references, panel_json, **direction_options)
    else:
        model = pipeline_config.model(model)
        ollama_url = pipeline_config.ollama_url(ollama_url)
        log(f"[1/4] Directing panel '{name}' via Ollama ({model})...")
        direct_fn = direct_fn or direct_panel.run_direction
        direction_options = dict(model=model, base_url=ollama_url)
        if canon_instructions is not None:
            direction_options["canon_instructions"] = canon_instructions
        direct_fn(brief, references, panel_json, **direction_options)

    render_references = list(references)
    if render_reference is not None:
        promoted = Path(render_reference).resolve()
        render_references = [promoted] + [r for r in render_references if Path(r).resolve() != promoted]
        log(
            "[3/4] --render-reference is deprecated: it now promotes that file to the "
            "first render reference instead of dropping the others."
        )
    if len(render_references) > max_render_references:
        render_references = render_references[:max_render_references]

    def _render() -> list[Path]:
        log("[2/4] Checking ComfyUI...")
        try:
            comfy_client.health(comfy_url)
        except RuntimeError as error:
            raise RuntimeError(
                f"{error} Start ComfyUI first (see README.md), then re-run this command."
            ) from error

        log(f"[3/4] Uploading {len(render_references)} reference(s) and submitting render...")
        reference_names = [
            comfy_client.upload_image(reference, comfy_url, subfolder=subfolder)
            for reference in render_references
        ]
        workflow = comfy_client.prepare(
            panel_json,
            reference_names,
            workflow_json,
            seed=seed,
            filename_prefix=f"{subfolder}/{name}",
            width=width,
            height=height,
            max_render_references=max_render_references,
        )
        prompt_id = comfy_client.submit(workflow, comfy_url)

        log(f"[4/4] Rendering (prompt {prompt_id})...")
        entry = comfy_client.wait_for_completion(prompt_id, comfy_url, timeout=render_timeout)
        images = comfy_client.output_images(entry)
        if not images:
            raise RuntimeError(f"ComfyUI finished prompt {prompt_id} but produced no images")

        output_root = pipeline_config.comfy_output_root(comfy_output_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved_paths = []
        for image in images:
            source = output_root / image.get("subfolder", "") / image["filename"]
            filename = f"{name}{Path(image['filename']).suffix}" if output_subdir is not None and len(images) == 1 else image["filename"]
            destination = output_dir / filename
            shutil.copyfile(source, destination)
            saved_paths.append(destination)
        return saved_paths

    # ComfyUI's known VRAM leak (see README's Known limitations) makes every
    # call in _render() fail the same way once it happens, including the
    # health check -- so one restart-and-retry cycle covers all of them.
    # ValueError from an earlier stage (bad input) never reaches here, so
    # this can't mask a permanent failure as a retryable one.
    try:
        return _render()
    except (RuntimeError, TimeoutError) as error:
        if recover is None:
            raise
        log(f"[recover] Render failed ({error}); attempting automatic ComfyUI recovery...")
        try:
            recover()
        except Exception as recovery_error:
            raise RuntimeError(
                f"{error} (automatic recovery also failed: {recovery_error})"
            ) from error
        log("[recover] Recovery finished; retrying the render once...")
        return _render()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="Project directory (briefs/, references/, requests/, outputs/)")
    parser.add_argument("--name", required=True, help="Panel name; brief defaults to <project>/briefs/<name>.txt")
    parser.add_argument("--reference", required=True, action="append", type=Path, help="Reference image; repeatable")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--brief", type=Path, help="Override the default brief path")
    parser.add_argument("--render-reference", type=Path, help="Reference image used for the ComfyUI render (default: first --reference)")
    parser.add_argument(
        "--director",
        choices=("local", "cloud"),
        default="local",
        help="'local' (default): Ollama/Qwen3-VL. 'cloud': a hosted model via cloud_director.py.",
    )
    # These default to None so pipeline_config can apply environment/config-file
    # values; passing a flag still wins over both.
    parser.add_argument("--model", help=f"Ollama director model (default: {pipeline_config.DEFAULT_MODEL})")
    parser.add_argument("--ollama-url", help=f"default: {pipeline_config.DEFAULT_OLLAMA_URL}")
    parser.add_argument("--cloud-provider", help=f"--director cloud only (default: {pipeline_config.DEFAULT_CLOUD_DIRECTOR_PROVIDER})")
    parser.add_argument("--cloud-model", help=f"--director cloud only (default: {pipeline_config.DEFAULT_CLOUD_DIRECTOR_MODEL})")
    parser.add_argument("--comfy-url", help=f"default: {pipeline_config.DEFAULT_COMFY_URL}")
    parser.add_argument("--comfy-output-root", type=Path, help="Where ComfyUI writes images (default: <comfy_root>/output)")
    parser.add_argument("--render-timeout", type=float, default=180.0)
    parser.add_argument("--width", type=int, help="Output width (default: sized from the first reference)")
    parser.add_argument("--height", type=int, help="Output height (default: sized from the first reference)")
    parser.add_argument(
        "--max-render-references",
        type=int,
        default=comfy_client.DEFAULT_MAX_RENDER_REFERENCES,
        help=f"Cap on references sent to ComfyUI (default: {comfy_client.DEFAULT_MAX_RENDER_REFERENCES})",
    )
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
        saved_paths = make_panel(
            args.project,
            args.name,
            args.reference,
            seed=args.seed,
            brief_path=args.brief,
            render_reference=args.render_reference,
            director=args.director,
            model=args.model,
            ollama_url=args.ollama_url,
            cloud_provider=args.cloud_provider,
            cloud_model=args.cloud_model,
            comfy_url=comfy_url,
            comfy_output_root=args.comfy_output_root,
            render_timeout=args.render_timeout,
            width=args.width,
            height=args.height,
            max_render_references=args.max_render_references,
            recover=recover,
            log=lambda message: print(message, file=sys.stderr),
        )
    except (ValueError, RuntimeError, TimeoutError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in saved_paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
