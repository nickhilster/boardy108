"""Turn a panel brief and visual references into structured art direction
using a hosted cloud model instead of the local Ollama/Qwen3-VL director.

Same contract as direct_panel.run_direction(): reads a brief and reference
images, writes a schema-validated panel JSON to output_path, and returns it.
make_panel.py and render_project.py select between the two via a `director`
setting; neither one has to know which cloud provider or model is behind
this module -- that's resolved here, from pipeline_config, by name. Adding a
second provider (OpenAI, Gemini, ...) means adding one function and one
PROVIDERS entry, not touching any caller.

The concrete network call is injectable via the `call` parameter specifically
so this can be unit tested without a real API key or network access -- the
same pattern make_panel.py uses for `recover`. The one real provider
(Anthropic) is exercised in tests against a local stub server standing in
for api.anthropic.com, so the request/response wire format is genuinely
covered even though no live call is made.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

import direct_panel
import pipeline_config

DEFAULT_PROVIDER = "anthropic"
SESSION_PROVIDER = "session"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_ATTEMPTS = 3
ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# A provider call takes the brief text, the encoded reference images, and the
# panel JSON schema, and returns the panel as a plain dict -- not yet
# validated against the schema, and not necessarily JSON-text at this point
# (a provider with native structured-output support, like Anthropic's tool
# use, can hand back an already-parsed object).
ProviderCall = Callable[..., dict[str, Any]]


def _encode_reference(path: Path) -> dict[str, str]:
    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(f"Unsupported reference image type for a cloud director: {path.suffix}")
    return {"media_type": media_type, "data": base64.b64encode(path.read_bytes()).decode("ascii")}


def _anthropic_call(
    brief_text: str,
    encoded_images: list[dict[str, str]],
    schema: dict[str, Any],
    *,
    model: str,
    api_key: str,
    api_base_url: str = ANTHROPIC_API_BASE_URL,
    timeout: float = 120.0,
    canon_instructions: str | None = None,
) -> dict[str, Any]:
    """Direct one panel via Anthropic's Messages API, using tool use to force
    the response to match panel_schema.json instead of parsing free-form text.
    """
    tool_schema = {key: value for key, value in schema.items() if key != "$schema"}
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Act as a comic art director. Study every supplied reference image, then convert "
                "the panel brief into the requested tool call. Preserve identity, costume, "
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
                + (
                    "When a scene brief conflicts with the locked visual canon or a prohibition, the "
                    "canon takes priority. Preserve the scene's non-conflicting intent and adapt only "
                    "the conflicting detail; never depict a prohibited feature.\n\n"
                    f"{canon_instructions}\n\n"
                    if canon_instructions is not None
                    else ""
                )
                + f"PANEL BRIEF:\n{brief_text if canon_instructions is not None else brief_text.strip()}"
            ),
        }
    ]
    for image in encoded_images:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": image["media_type"], "data": image["data"]},
            }
        )
    payload = {
        "model": model,
        "max_tokens": 2000,
        "tools": [
            {
                "name": "submit_panel_direction",
                "description": "Submit the structured panel direction matching the required contract.",
                "input_schema": tool_schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": "submit_panel_direction"},
        "messages": [{"role": "user", "content": content}],
    }
    request = urllib.request.Request(
        f"{api_base_url.rstrip('/')}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(f"Cloud director request failed for {api_base_url}: {error}") from error
    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "submit_panel_direction":
            return block.get("input", {})
    raise RuntimeError("Cloud director response did not contain the expected tool call")


PROVIDERS: dict[str, ProviderCall] = {
    "anthropic": _anthropic_call,
}


def run_direction(
    brief_path: Path | str,
    references: Iterable[Path | str],
    output_path: Path | str,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    call: ProviderCall | None = None,
    canon_instructions: str | None = None,
) -> dict[str, Any]:
    """Direct a panel using a hosted cloud model instead of Ollama.

    `call` overrides the provider registry entirely -- pass a fake for tests,
    or a differently-configured real call. Left unset, the provider/model/key
    resolve from pipeline_config and PROVIDERS, exactly like the local
    director resolves its own settings.
    """
    brief_file = Path(brief_path).resolve()
    if not brief_file.is_file():
        raise ValueError(f"Panel brief does not exist: {brief_file}")
    brief_text = brief_file.read_text(encoding="utf-8")

    reference_paths = direct_panel.validate_reference_paths(references)
    encoded_images = [_encode_reference(path) for path in reference_paths]
    schema = direct_panel.load_schema()

    provider_name = pipeline_config.get("cloud_director_provider", explicit=provider, default=DEFAULT_PROVIDER)
    if call is None and provider_name == SESSION_PROVIDER:
        # The art director is the Claude Code session itself: it authors one
        # schema-valid direction per scene into <project>/directions/<scene>.json
        # ahead of the run; no network call or API key is involved.
        direction_path = Path(output_path).resolve().parent.parent / "directions" / f"{Path(output_path).stem}.json"

        def call(_text: str, _images: list[dict[str, str]], _schema: dict[str, Any]) -> dict[str, Any]:
            if not direction_path.is_file():
                raise RuntimeError(f"Session direction missing: {direction_path}")
            return json.loads(direction_path.read_text(encoding="utf-8"))

    if call is None:
        provider_call = PROVIDERS.get(provider_name)
        if provider_call is None:
            raise ValueError(f"Unknown cloud director provider: {provider_name!r} (known: {sorted(PROVIDERS)})")
        resolved_model = pipeline_config.get("cloud_director_model", explicit=model, default=DEFAULT_MODEL)
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Cloud director needs an API key: set the ANTHROPIC_API_KEY environment variable."
            )

        def call(text: str, images: list[dict[str, str]], sch: dict[str, Any]) -> dict[str, Any]:
            options: dict[str, Any] = dict(model=resolved_model, api_key=resolved_key)
            if canon_instructions is not None:
                options["canon_instructions"] = canon_instructions
            return provider_call(text, images, sch, **options)

    last_error: ValueError | None = None
    for _attempt in range(max_attempts):
        try:
            panel = call(brief_text, encoded_images, schema)
            validated = direct_panel.validate_panel(panel)
            break
        except ValueError as error:
            last_error = error
    else:
        assert last_error is not None
        raise last_error

    direct_panel.write_json_atomic(Path(output_path).resolve(), validated)
    return validated


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--reference", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provider", help=f"default: {DEFAULT_PROVIDER}")
    parser.add_argument("--model", help=f"default: {DEFAULT_MODEL}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        panel = run_direction(
            args.brief,
            args.reference,
            args.output,
            provider=args.provider,
            model=args.model,
        )
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(panel, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
