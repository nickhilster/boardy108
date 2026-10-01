"""Prepare and submit local FLUX.2 Klein workflows to ComfyUI."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from direct_panel import validate_panel, write_json_atomic


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {"Content-Type": content_type} if content_type else {}
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(f"ComfyUI request failed for {url}: {error}") from error


def health(base_url: str = "http://127.0.0.1:8188") -> dict[str, Any]:
    base = base_url.rstrip("/")
    stats = request_json(f"{base}/system_stats")
    objects = request_json(f"{base}/object_info", timeout=60)
    required = [name for name in ("UNETLoader", "CLIPLoader", "VAELoader") if name in objects]
    if len(required) != 3:
        missing = sorted({"UNETLoader", "CLIPLoader", "VAELoader"} - set(required))
        raise RuntimeError(f"ComfyUI is missing required core loaders: {', '.join(missing)}")
    return {"system_stats": stats, "required_loaders": required}


def panel_prompt(panel: dict[str, Any]) -> str:
    validated = validate_panel(panel)
    return "\n".join(
        [
            validated["positive_prompt"],
            f"Camera: {validated['camera']}",
            f"Composition: {validated['composition']}",
            f"Characters: {', '.join(validated['characters'])}",
            f"Locked visual traits: {'; '.join(validated['locked_traits'])}",
            f"Lighting: {validated['lighting']}",
            f"Continuity: {validated['continuity']}",
            "No lettering, captions, speech bubbles, watermarks, or signatures.",
        ]
    )


DEFAULT_MAX_RENDER_REFERENCES = 3


def build_flux_edit_workflow(
    panel: dict[str, Any],
    reference_names: str | list[str],
    *,
    seed: int,
    filename_prefix: str,
    width: int | None = None,
    height: int | None = None,
    max_render_references: int = DEFAULT_MAX_RENDER_REFERENCES,
) -> dict[str, Any]:
    """Build a panel's FLUX.2 Klein edit workflow, using panel_prompt(panel) as
    the conditioning text. See build_reference_edit_workflow for the
    underlying graph construction, which this just supplies a prompt to --
    it's reused as-is for meme remixing with a plain prompt string instead of
    a full panel dict.
    """
    return build_reference_edit_workflow(
        panel_prompt(panel),
        reference_names,
        seed=seed,
        filename_prefix=filename_prefix,
        width=width,
        height=height,
        max_render_references=max_render_references,
    )


def build_reference_edit_workflow(
    prompt: str,
    reference_names: str | list[str],
    *,
    seed: int,
    filename_prefix: str,
    width: int | None = None,
    height: int | None = None,
    max_render_references: int = DEFAULT_MAX_RENDER_REFERENCES,
) -> dict[str, Any]:
    """Build a stock-node FLUX.2 Klein edit workflow anchored on 1-N reference images.

    A single reference produces the exact node graph this pipeline has always
    used (node IDs 4-11 for the reference branch), so existing callers and
    saved workflow JSON stay valid. Additional references extend the chain
    with new node IDs (20+) rather than renumbering the fixed nodes, and each
    one adds its own positive/negative `ReferenceLatent`, chained from the
    previous one, per the stock-node multi-reference design.
    """
    if isinstance(reference_names, str):
        reference_names = [reference_names]
    reference_names = list(reference_names)
    if not reference_names:
        raise ValueError("At least one reference image is required")
    for name in reference_names:
        if not name.strip():
            raise ValueError("ComfyUI reference image name cannot be empty")
    if len(reference_names) > max_render_references:
        raise ValueError(
            f"{len(reference_names)} render references exceeds the configured "
            f"maximum of {max_render_references}"
        )
    if seed < 0 or seed > 18446744073709551615:
        raise ValueError("Seed is outside ComfyUI's supported range")
    if (width is None) != (height is None):
        raise ValueError("width and height must be given together")

    workflow: dict[str, Any] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "flux-2-klein-4b-fp8.safetensors", "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "8": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}},
    }

    # First reference keeps the original fixed node IDs (4, 5, 9, 10, 11).
    # Explicit width/height skip GetImageSize (node 6) entirely so output
    # dimensions can be decoupled from any reference image's size; omitting
    # them falls back to sizing from the first reference, as before.
    workflow["4"] = {"class_type": "LoadImage", "inputs": {"image": reference_names[0]}}
    workflow["5"] = {
        "class_type": "ImageScaleToTotalPixels",
        "inputs": {
            "image": ["4", 0],
            "upscale_method": "nearest-exact",
            "megapixels": 1.0,
            "resolution_steps": 1,
        },
    }
    if width is None:
        workflow["6"] = {"class_type": "GetImageSize", "inputs": {"image": ["5", 0]}}
        size_width: Any = ["6", 0]
        size_height: Any = ["6", 1]
    else:
        size_width = width
        size_height = height
    workflow["9"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}}
    workflow["10"] = {
        "class_type": "ReferenceLatent",
        "inputs": {"conditioning": ["7", 0], "latent": ["9", 0]},
    }
    workflow["11"] = {
        "class_type": "ReferenceLatent",
        "inputs": {"conditioning": ["8", 0], "latent": ["9", 0]},
    }
    positive_conditioning: list[Any] = ["10", 0]
    negative_conditioning: list[Any] = ["11", 0]

    # Additional references are appended as new nodes, chained onto the
    # previous ReferenceLatent's conditioning output, per the design's
    # deterministic dynamic graph.
    next_id = 20
    for reference_name in reference_names[1:]:
        load_id, scale_id, encode_id, ref_pos_id, ref_neg_id = (str(next_id + offset) for offset in range(5))
        next_id += 5
        workflow[load_id] = {"class_type": "LoadImage", "inputs": {"image": reference_name}}
        workflow[scale_id] = {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {
                "image": [load_id, 0],
                "upscale_method": "nearest-exact",
                "megapixels": 1.0,
                "resolution_steps": 1,
            },
        }
        workflow[encode_id] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": [scale_id, 0], "vae": ["3", 0]},
        }
        workflow[ref_pos_id] = {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": positive_conditioning, "latent": [encode_id, 0]},
        }
        workflow[ref_neg_id] = {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": negative_conditioning, "latent": [encode_id, 0]},
        }
        positive_conditioning = [ref_pos_id, 0]
        negative_conditioning = [ref_neg_id, 0]

    workflow["12"] = {
        "class_type": "CFGGuider",
        "inputs": {
            "model": ["1", 0],
            "positive": positive_conditioning,
            "negative": negative_conditioning,
            "cfg": 1.0,
        },
    }
    workflow["13"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    workflow["14"] = {
        "class_type": "Flux2Scheduler",
        "inputs": {"steps": 4, "width": size_width, "height": size_height},
    }
    workflow["15"] = {
        "class_type": "EmptyFlux2LatentImage",
        "inputs": {"width": size_width, "height": size_height, "batch_size": 1},
    }
    workflow["16"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    workflow["17"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["16", 0],
            "guider": ["12", 0],
            "sampler": ["13", 0],
            "sigmas": ["14", 0],
            "latent_image": ["15", 0],
        },
    }
    workflow["18"] = {"class_type": "VAEDecode", "inputs": {"samples": ["17", 0], "vae": ["3", 0]}}
    workflow["19"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["18", 0], "filename_prefix": filename_prefix},
    }
    return validate_api_workflow(workflow)


def validate_api_workflow(workflow: Any) -> dict[str, Any]:
    if not isinstance(workflow, dict) or not workflow:
        raise ValueError("API workflow must be a non-empty JSON object")
    for node_id, node in workflow.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            raise ValueError("Every workflow node must have a string ID and object value")
        if not isinstance(node.get("class_type"), str) or not node["class_type"]:
            raise ValueError(f"Workflow node {node_id} is missing class_type")
        if not isinstance(node.get("inputs"), dict):
            raise ValueError(f"Workflow node {node_id} is missing inputs")
    return workflow


def prepare(
    panel_path: Path | str,
    reference_names: str | list[str],
    output_path: Path | str,
    *,
    seed: int,
    filename_prefix: str,
    width: int | None = None,
    height: int | None = None,
    max_render_references: int = DEFAULT_MAX_RENDER_REFERENCES,
) -> dict[str, Any]:
    panel = json.loads(Path(panel_path).read_text(encoding="utf-8"))
    workflow = build_flux_edit_workflow(
        panel,
        reference_names,
        seed=seed,
        filename_prefix=filename_prefix,
        width=width,
        height=height,
        max_render_references=max_render_references,
    )
    write_json_atomic(Path(output_path).resolve(), workflow)
    return workflow


def prepare_reference_edit(
    prompt: str,
    reference_names: str | list[str],
    output_path: Path | str,
    *,
    seed: int,
    filename_prefix: str,
    width: int | None = None,
    height: int | None = None,
    max_render_references: int = DEFAULT_MAX_RENDER_REFERENCES,
) -> dict[str, Any]:
    """Same as prepare(), but for a plain prompt string instead of a panel JSON
    file -- used by make_meme.py to remix a meme template's artwork."""
    workflow = build_reference_edit_workflow(
        prompt,
        reference_names,
        seed=seed,
        filename_prefix=filename_prefix,
        width=width,
        height=height,
        max_render_references=max_render_references,
    )
    write_json_atomic(Path(output_path).resolve(), workflow)
    return workflow


def submit(
    workflow: dict[str, Any],
    base_url: str = "http://127.0.0.1:8188",
    *,
    client_id: str | None = None,
) -> str:
    validated = validate_api_workflow(workflow)
    payload = json.dumps(
        {"prompt": validated, "client_id": client_id or str(uuid.uuid4())}
    ).encode("utf-8")
    response = request_json(
        f"{base_url.rstrip('/')}/prompt",
        method="POST",
        body=payload,
        content_type="application/json",
        timeout=60,
    )
    if response.get("node_errors"):
        raise RuntimeError(f"ComfyUI rejected workflow nodes: {json.dumps(response['node_errors'])}")
    prompt_id = response.get("prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id:
        raise RuntimeError("ComfyUI response did not include a prompt_id")
    return prompt_id


def upload_image(
    image_path: Path | str,
    base_url: str = "http://127.0.0.1:8188",
    *,
    subfolder: str = "comic-pipeline",
) -> str:
    image = Path(image_path).resolve()
    if not image.is_file():
        raise ValueError(f"Reference image does not exist: {image}")
    boundary = f"----ComicPipeline{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    parts = []
    for name, value in (("subfolder", subfolder), ("type", "input"), ("overwrite", "true")):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{image.name}\"\r\nContent-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    parts.append(image.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    response = request_json(
        f"{base_url.rstrip('/')}/upload/image",
        method="POST",
        body=b"".join(parts),
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=120,
    )
    name = response.get("name")
    returned_subfolder = response.get("subfolder", "")
    if not isinstance(name, str) or not name:
        raise RuntimeError("ComfyUI upload response did not include an image name")
    return f"{returned_subfolder}/{name}" if returned_subfolder else name


def wait_for_completion(
    prompt_id: str,
    base_url: str = "http://127.0.0.1:8188",
    *,
    timeout: float = 180.0,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    deadline = time.monotonic() + timeout
    while True:
        history = request_json(f"{base}/history/{prompt_id}", timeout=10)
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI reported an error for prompt {prompt_id}: {json.dumps(status)}")
            if status.get("completed"):
                return entry
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for ComfyUI prompt {prompt_id} to complete")
        time.sleep(poll_interval)


def output_images(history_entry: dict[str, Any]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for node_output in history_entry.get("outputs", {}).values():
        images.extend(node_output.get("images", []))
    return images


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    upload = commands.add_parser("upload")
    upload.add_argument("--reference", required=True, type=Path)
    upload.add_argument("--subfolder", default="comic-pipeline")
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--panel-json", required=True, type=Path)
    prepare_parser.add_argument("--reference-name", required=True, action="append")
    prepare_parser.add_argument("--output", required=True, type=Path)
    prepare_parser.add_argument("--seed", type=int, default=0)
    prepare_parser.add_argument("--filename-prefix", default="comic-pipeline/panel")
    prepare_parser.add_argument("--width", type=int)
    prepare_parser.add_argument("--height", type=int)
    prepare_parser.add_argument("--max-render-references", type=int, default=DEFAULT_MAX_RENDER_REFERENCES)
    submit_parser = commands.add_parser("submit")
    submit_parser.add_argument("--api-workflow", required=True, type=Path)
    wait_parser = commands.add_parser("wait")
    wait_parser.add_argument("--prompt-id", required=True)
    wait_parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "health":
            result: Any = health(args.comfy_url)
        elif args.command == "upload":
            result = {"reference_name": upload_image(args.reference, args.comfy_url, subfolder=args.subfolder)}
        elif args.command == "prepare":
            workflow = prepare(
                args.panel_json,
                args.reference_name,
                args.output,
                seed=args.seed,
                filename_prefix=args.filename_prefix,
                width=args.width,
                height=args.height,
                max_render_references=args.max_render_references,
            )
            result = {"output": str(args.output.resolve()), "nodes": len(workflow)}
        elif args.command == "submit":
            workflow = json.loads(args.api_workflow.read_text(encoding="utf-8"))
            result = {"prompt_id": submit(workflow, args.comfy_url)}
        else:
            entry = wait_for_completion(args.prompt_id, args.comfy_url, timeout=args.timeout)
            result = {"status": entry.get("status", {}), "images": output_images(entry)}
    except (ValueError, RuntimeError, TimeoutError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
