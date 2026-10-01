"""Turn a panel brief and visual references into structured local art direction."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable


SCHEMA_PATH = Path(__file__).with_name("panel_schema.json")
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# String fields the director may legitimately leave empty: negative_prompt is
# advisory only (the renderer never reads it), and continuity has nothing to
# say for a standalone panel with no prior panel or sequence to relate to.
OPTIONAL_CONTENT_FIELDS = {"negative_prompt", "continuity"}


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_reference_paths(paths: Iterable[Path | str]) -> list[Path]:
    references = [Path(path).resolve() for path in paths]
    if not references:
        raise ValueError("At least one reference image is required")
    for reference in references:
        if not reference.is_file():
            raise ValueError(f"Reference image does not exist: {reference}")
        if reference.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
            allowed = ", ".join(sorted(ALLOWED_IMAGE_SUFFIXES))
            raise ValueError(f"Unsupported reference image type: {reference.suffix}; use {allowed}")
    return references


def build_request(
    model: str, brief: str, references: Iterable[Path | str], *,
    canon_instructions: str | None = None,
) -> dict[str, Any]:
    reference_paths = validate_reference_paths(references)
    if not brief.strip():
        raise ValueError("Panel brief cannot be empty")
    encoded_images = [
        base64.b64encode(reference.read_bytes()).decode("ascii") for reference in reference_paths
    ]
    return {
        "model": model,
        "stream": False,
        "keep_alive": 0,
        "format": load_schema(),
        "think": False,
        "options": {
            "num_ctx": 8192,
            "num_predict": 1600,
            "temperature": 0.3,
            "repeat_penalty": 1.3,
            "repeat_last_n": 128,
        },
        "messages": [
            {
                "role": "user",
                "content": (
                    "Act as a comic art director. Study every supplied reference image, then convert "
                    "the panel brief into the requested JSON contract. Preserve identity, costume, "
                    "location, and continuity details visible in the references, and their palette too "
                    "unless the brief calls for a different rendering medium, color count, or art style. "
                    "Describe one frame only. Do not include dialogue or lettering in the image prompt.\n\n"
                    "The renderer only ever reads positive_prompt, camera, composition, characters, "
                    "locked_traits, and lighting -- negative_prompt is shown to a human reviewer but is "
                    "never applied to the image. If the brief requires excluding or restricting something "
                    "(a color palette, a medium, photographic realism, a technique), state that "
                    "requirement affirmatively inside positive_prompt, not only in negative_prompt. "
                    "If the brief describes a standalone image with no prior panel or sequence, leave "
                    "continuity as an empty string rather than inventing one.\n\n"
                    "When a scene brief conflicts with the locked visual canon or a prohibition, the "
                    "canon takes priority. Preserve the scene's non-conflicting intent and adapt only "
                    "the conflicting detail; never depict a prohibited feature.\n\n"
                    + (f"{canon_instructions}\n\n" if canon_instructions is not None else "")
                    + f"PANEL BRIEF:\n{brief if canon_instructions is not None else brief.strip()}"
                ),
                "images": encoded_images,
            }
        ],
    }


def validate_panel(panel: Any) -> dict[str, Any]:
    schema = load_schema()
    if not isinstance(panel, dict):
        raise ValueError("Panel direction must be a JSON object")
    for field in schema["required"]:
        if field not in panel:
            raise ValueError(f"Panel direction is missing required field: {field}")
        expected_type = schema["properties"][field]["type"]
        if expected_type == "string":
            if not isinstance(panel[field], str):
                raise ValueError(f"Panel field must be a string: {field}")
            if field not in OPTIONAL_CONTENT_FIELDS and not panel[field].strip():
                raise ValueError(f"Panel field must be a non-empty string: {field}")
        if expected_type == "array" and not isinstance(panel[field], list):
            raise ValueError(f"Panel field must be an array: {field}")
    return {field: panel[field] for field in schema["required"]}


def post_json(url: str, payload: dict[str, Any], timeout: int = 300) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(f"Local service request failed for {url}: {error}") from error


def unload_model(base_url: str, model: str) -> None:
    post_json(f"{base_url.rstrip('/')}/api/generate", {"model": model, "keep_alive": 0}, timeout=30)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temp:
            json.dump(value, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def request_panel(payload: dict[str, Any], base_url: str) -> dict[str, Any]:
    response = post_json(f"{base_url.rstrip('/')}/api/chat", payload)
    message = response.get("message", {})
    # qwen3-vl routes structured-output JSON into "thinking" instead of
    # "content" when thinking is disabled with a JSON schema format.
    content = message.get("content") or message.get("thinking")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Ollama response did not contain assistant content")
    try:
        panel = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("Ollama response was not valid JSON") from error
    return validate_panel(panel)


def run_direction(
    brief_path: Path | str,
    references: Iterable[Path | str],
    output_path: Path | str,
    *,
    model: str = "qwen3-vl:8b",
    base_url: str = "http://127.0.0.1:11434",
    max_attempts: int = 5,
    canon_instructions: str | None = None,
) -> dict[str, Any]:
    brief_file = Path(brief_path).resolve()
    if not brief_file.is_file():
        raise ValueError(f"Panel brief does not exist: {brief_file}")
    brief = brief_file.read_text(encoding="utf-8")
    if canon_instructions is None:
        payload = build_request(model, brief, references)
    else:
        payload = build_request(model, brief, references, canon_instructions=canon_instructions)
    try:
        last_error: ValueError | None = None
        for attempt in range(max_attempts):
            try:
                validated = request_panel(payload, base_url)
                break
            except ValueError as error:
                last_error = error
        else:
            assert last_error is not None
            raise last_error
        write_json_atomic(Path(output_path).resolve(), validated)
        return validated
    finally:
        unload_model(base_url, model)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--reference", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="qwen3-vl:8b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        panel = run_direction(
            args.brief,
            args.reference,
            args.output,
            model=args.model,
            base_url=args.ollama_url,
        )
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(panel, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
