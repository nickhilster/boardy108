"""Machine-specific settings for the panel pipeline.

Nothing here should be baked into the scripts: where ComfyUI lives, where it
writes output, and which endpoints/model to talk to all vary by machine. This
module resolves them in one place so the rest of the pipeline stays portable.

Precedence, highest first:

1. An explicit argument (CLI flag, or a keyword passed in Python).
2. An environment variable.
3. A JSON config file.
4. A built-in default, where a sensible machine-independent one exists.

`comfy_root` has no built-in default on purpose -- there is no correct guess
for it, and silently guessing wrong produces a confusing failure much later.

Config file lookup, first match wins:

1. $PANELFORGE_CONFIG
2. pipeline.config.json beside this module
3. ~/.panelforge.json

Environment variables: PANELFORGE_COMFY_ROOT, PANELFORGE_COMFY_OUTPUT_ROOT,
PANELFORGE_COMFY_URL, PANELFORGE_OLLAMA_URL, PANELFORGE_MODEL,
PANELFORGE_MEME_FONT, PANELFORGE_MEME_INTAKE_PORT.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:8b"
DEFAULT_MEME_INTAKE_PORT = 8765
DEFAULT_CLOUD_DIRECTOR_PROVIDER = "anthropic"
DEFAULT_CLOUD_DIRECTOR_MODEL = "claude-sonnet-5"

_ENV_PREFIX = "PANELFORGE_"
_CONFIG_BASENAME = "pipeline.config.json"


class ConfigError(RuntimeError):
    """A required setting could not be resolved."""


def config_path() -> Path | None:
    """Return the config file that will be used, or None if there isn't one."""
    override = os.environ.get(f"{_ENV_PREFIX}CONFIG")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise ConfigError(
                f"{_ENV_PREFIX}CONFIG points at {path}, which is not a file."
            )
        return path
    for candidate in (
        Path(__file__).resolve().parent / _CONFIG_BASENAME,
        Path.home() / ".panelforge.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def load_file() -> dict[str, Any]:
    path = config_path()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object at the top level.")
    return data


def get(key: str, *, explicit: Any = None, default: Any = None) -> Any:
    """Resolve one setting. `key` is the snake_case config-file key."""
    if explicit is not None:
        return explicit
    env = os.environ.get(f"{_ENV_PREFIX}{key.upper()}")
    if env:
        return env
    value = load_file().get(key)
    if value is not None:
        return value
    return default


def require(key: str, *, explicit: Any = None) -> Any:
    value = get(key, explicit=explicit)
    if value is None:
        seen = config_path()
        where = f"{seen}" if seen else (
            f"no config file found (looked for {_CONFIG_BASENAME} beside the "
            f"pipeline scripts and ~/.panelforge.json)"
        )
        raise ConfigError(
            f"'{key}' is not configured. Set {_ENV_PREFIX}{key.upper()}, or add "
            f'"{key}" to a config file -- {where}. '
            f"See {_CONFIG_BASENAME.replace('.json', '.example.json')}."
        )
    return value


def comfy_root(explicit: Any = None) -> Path:
    """Where ComfyUI is installed. Required; no default is guessed."""
    return Path(require("comfy_root", explicit=explicit)).expanduser()


def comfy_output_root(explicit: Any = None) -> Path:
    """Where ComfyUI writes rendered images. Defaults to <comfy_root>/output."""
    if explicit is not None:
        return Path(explicit).expanduser()
    configured = get("comfy_output_root")
    if configured is not None:
        return Path(configured).expanduser()
    return comfy_root() / "output"


def comfy_url(explicit: Any = None) -> str:
    return str(get("comfy_url", explicit=explicit, default=DEFAULT_COMFY_URL))


def ollama_url(explicit: Any = None) -> str:
    return str(get("ollama_url", explicit=explicit, default=DEFAULT_OLLAMA_URL))


def model(explicit: Any = None) -> str:
    return str(get("model", explicit=explicit, default=DEFAULT_MODEL))


def meme_intake_port(explicit: Any = None) -> int:
    return int(get("meme_intake_port", explicit=explicit, default=DEFAULT_MEME_INTAKE_PORT))


def cloud_director_provider(explicit: Any = None) -> str:
    return str(get("cloud_director_provider", explicit=explicit, default=DEFAULT_CLOUD_DIRECTOR_PROVIDER))


def cloud_director_model(explicit: Any = None) -> str:
    return str(get("cloud_director_model", explicit=explicit, default=DEFAULT_CLOUD_DIRECTOR_MODEL))
