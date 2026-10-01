"""Fetch and cache meme template metadata/images from external sources.

Imgflip's get_memes endpoint is the only automated fetcher in v1 (free,
no-auth, ~100 popular templates). See docs/MEME-SOURCES.md for the researched
list of other sources and why they aren't automated yet.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_IMGFLIP_BASE_URL = "https://api.imgflip.com"

# Imgflip returns 403 Forbidden for urllib's default "Python-urllib/x.y"
# User-Agent (confirmed against the live API; curl and browser UAs are fine).
# Any identifiable, non-default UA clears it.
USER_AGENT = "PanelForge-MemeSources/1.0 (local pipeline, non-commercial)"


def _get_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(f"Meme source request failed for {url}: {error}") from error


def normalize_template(raw: dict[str, Any], *, source: str = "imgflip") -> dict[str, Any]:
    for field in ("id", "name", "url", "width", "height", "box_count"):
        if field not in raw:
            raise ValueError(f"Template record is missing field: {field}")
    return {
        "source": source,
        "source_id": str(raw["id"]),
        "name": raw["name"],
        "source_url": raw["url"],
        "width": int(raw["width"]),
        "height": int(raw["height"]),
        "box_count": int(raw["box_count"]),
    }


def fetch_top_templates(
    *, base_url: str = DEFAULT_IMGFLIP_BASE_URL, limit: int = 100, timeout: int = 30
) -> list[dict[str, Any]]:
    response = _get_json(f"{base_url.rstrip('/')}/get_memes", timeout=timeout)
    if not response.get("success"):
        raise RuntimeError(f"Imgflip get_memes reported failure: {response}")
    memes = response.get("data", {}).get("memes", [])
    if not isinstance(memes, list):
        raise RuntimeError("Imgflip get_memes response did not contain a memes list")
    return [normalize_template(raw) for raw in memes[:limit]]


def pick_template(
    templates: list[dict[str, Any]],
    *,
    source_id: str | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    if not templates:
        raise ValueError("No templates available to pick from")
    if source_id is not None:
        for template in templates:
            if template["source_id"] == str(source_id):
                return template
        raise ValueError(f"No template found with source_id={source_id!r}")
    chooser = rng or random
    return chooser.choice(templates)


def download_template(
    template: dict[str, Any], cache_dir: Path | str, *, timeout: int = 30
) -> tuple[Path, str]:
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(template["source_url"], method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except urllib.error.URLError as error:
        raise RuntimeError(f"Template download failed for {template['source_url']}: {error}") from error

    digest = hashlib.sha256(content).hexdigest()
    suffix = Path(template["source_url"]).suffix or ".jpg"
    destination = cache_dir / f"{digest}{suffix}"
    if not destination.is_file():
        handle, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=cache_dir)
        try:
            with os.fdopen(handle, "wb") as temp:
                temp.write(content)
            os.replace(temp_name, destination)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
    return destination, digest
