"""Turn a meme brief and a template image into a structured meme contract.

Mirrors direct_panel.py's shape (Ollama/Qwen3-VL, structured JSON output,
retry-then-unload), but the director's output is intentionally scoped to what
only the director should decide -- concept, whether to remix the artwork, and
captions. Template metadata (source, dimensions, box_count) is supplied by
meme_sources.py and merged in by make_meme.py, the same way panel_schema.json
never mentions reference image paths because those are supplied externally.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

from direct_panel import post_json, unload_model

SCHEMA_PATH = Path(__file__).with_name("meme_schema.json")


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def build_request(
    model: str,
    brief: str,
    template_image: Path | str,
    template_meta: dict[str, Any],
) -> dict[str, Any]:
    template_path = Path(template_image).resolve()
    if not template_path.is_file():
        raise ValueError(f"Template image does not exist: {template_path}")
    if not brief.strip():
        raise ValueError("Meme brief cannot be empty")
    box_count = template_meta["box_count"]
    if not isinstance(box_count, int) or box_count < 1:
        raise ValueError(f"Template box_count must be a positive integer: {box_count!r}")
    name = template_meta.get("name", "this template")
    encoded_image = base64.b64encode(template_path.read_bytes()).decode("ascii")

    return {
        "model": model,
        "stream": False,
        "keep_alive": 0,
        "format": load_schema(),
        "think": False,
        "options": {
            "num_ctx": 8192,
            "num_predict": 1200,
            "temperature": 0.5,
            "repeat_penalty": 1.3,
            "repeat_last_n": 128,
        },
        "messages": [
            {
                "role": "user",
                "content": (
                    "Act as a meme director. Study the supplied template image -- "
                    f"the meme format is '{name}' with exactly {box_count} text "
                    "box(es), numbered 0 to N-1 in the format's usual reading order. "
                    "Given the brief below, decide whether captioning the template "
                    "as-is lands the joke, or whether remixing the artwork itself "
                    "(swapping a face, prop, background detail, or scene element) "
                    "pushes the joke further while keeping the format recognizable. "
                    "Produce the requested JSON contract.\n\n"
                    "The remix engine only ever reads remix.positive_prompt -- "
                    "remix.locked_elements is shown to a human reviewer but is never "
                    "applied to the image. If the remixed image must keep something "
                    "recognizable (a pose, a layout, a palette), state that "
                    "requirement affirmatively inside remix.positive_prompt, not only "
                    "in locked_elements. If you decide not to remix, set remix.enabled "
                    "to false and leave remix.positive_prompt empty.\n\n"
                    f"Write exactly {box_count} entries in captions, one per "
                    f"box_index from 0 to {box_count - 1}. Write each caption's "
                    "text as the literal words that appear on the image, nothing "
                    "else -- never prefix it with a speaker label like '[Left]', "
                    "'[Right]', 'Person A:', or any other meta-annotation.\n\n"
                    "The remix engine (a diffusion image model) cannot render "
                    "legible text, numbers, or writing -- it produces garbled "
                    "gibberish whenever asked to. Never ask for a sign, label, "
                    "speech bubble, scoreboard, screen, or any other readable "
                    "text inside remix.positive_prompt. If the joke needs text "
                    "to be visible, put it in captions instead, never in the "
                    "remixed artwork.\n\n"
                    f"MEME BRIEF:\n{brief.strip()}"
                ),
                "images": [encoded_image],
            }
        ],
    }


def validate_meme(meme: Any) -> dict[str, Any]:
    schema = load_schema()
    if not isinstance(meme, dict):
        raise ValueError("Meme direction must be a JSON object")
    for field in schema["required"]:
        if field not in meme:
            raise ValueError(f"Meme direction is missing required field: {field}")

    concept_summary = meme["concept_summary"]
    if not isinstance(concept_summary, str) or not concept_summary.strip():
        raise ValueError("Meme field must be a non-empty string: concept_summary")

    remix = meme["remix"]
    if not isinstance(remix, dict):
        raise ValueError("Meme field 'remix' must be an object")
    for field in schema["properties"]["remix"]["required"]:
        if field not in remix:
            raise ValueError(f"Meme remix is missing required field: {field}")
    if not isinstance(remix["enabled"], bool):
        raise ValueError("Meme remix field must be a boolean: enabled")
    if not isinstance(remix["positive_prompt"], str):
        raise ValueError("Meme remix field must be a string: positive_prompt")
    if remix["enabled"] and not remix["positive_prompt"].strip():
        raise ValueError("Meme remix positive_prompt must be non-empty when enabled is true")
    if not isinstance(remix["locked_elements"], list) or not all(
        isinstance(item, str) for item in remix["locked_elements"]
    ):
        raise ValueError("Meme remix field must be an array of strings: locked_elements")

    captions = meme["captions"]
    if not isinstance(captions, list):
        raise ValueError("Meme field must be an array: captions")
    seen_indices: set[int] = set()
    normalized_captions = []
    for entry in captions:
        if not isinstance(entry, dict) or "box_index" not in entry or "text" not in entry:
            raise ValueError("Each caption must be an object with box_index and text")
        box_index = entry["box_index"]
        text = entry["text"]
        if not isinstance(box_index, int) or isinstance(box_index, bool):
            raise ValueError("Caption box_index must be an integer")
        if not isinstance(text, str):
            raise ValueError("Caption text must be a string")
        if box_index in seen_indices:
            raise ValueError(f"Duplicate caption box_index: {box_index}")
        seen_indices.add(box_index)
        normalized_captions.append({"box_index": box_index, "text": text})
    normalized_captions.sort(key=lambda entry: entry["box_index"])

    return {
        "concept_summary": concept_summary,
        "remix": {
            "enabled": remix["enabled"],
            "positive_prompt": remix["positive_prompt"],
            "locked_elements": list(remix["locked_elements"]),
        },
        "captions": normalized_captions,
    }


def request_meme(payload: dict[str, Any], base_url: str, *, box_count: int) -> dict[str, Any]:
    response = post_json(f"{base_url.rstrip('/')}/api/chat", payload)
    message = response.get("message", {})
    # qwen3-vl routes structured-output JSON into "thinking" instead of
    # "content" when thinking is disabled with a JSON schema format -- same
    # quirk documented in direct_panel.py.
    content = message.get("content") or message.get("thinking")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Ollama response did not contain assistant content")
    try:
        meme = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("Ollama response was not valid JSON") from error
    validated = validate_meme(meme)
    indices = {entry["box_index"] for entry in validated["captions"]}
    if indices != set(range(box_count)):
        raise ValueError(
            f"Captions must cover box_index 0..{box_count - 1} exactly; got {sorted(indices)}"
        )
    return validated


def run_direction(
    template_image: Path | str,
    template_meta: dict[str, Any],
    brief: str,
    *,
    model: str = "qwen3-vl:8b",
    base_url: str = "http://127.0.0.1:11434",
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Direct a meme. Returns the validated director dict (not written to disk --
    make_meme.py merges in template metadata before writing the combined
    contract/provenance record)."""
    payload = build_request(model, brief, template_image, template_meta)
    box_count = template_meta["box_count"]
    try:
        last_error: ValueError | None = None
        for _attempt in range(max_attempts):
            try:
                return request_meme(payload, base_url, box_count=box_count)
            except ValueError as error:
                last_error = error
        assert last_error is not None
        raise last_error
    finally:
        unload_model(base_url, model)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--template-image", required=True, type=Path)
    parser.add_argument("--template-name", default="unknown template")
    parser.add_argument("--box-count", required=True, type=int)
    parser.add_argument("--model", default="qwen3-vl:8b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    brief_text = args.brief.read_text(encoding="utf-8")
    template_meta = {"name": args.template_name, "box_count": args.box_count}
    try:
        meme = run_direction(
            args.template_image,
            template_meta,
            brief_text,
            model=args.model,
            base_url=args.ollama_url,
        )
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(meme, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
