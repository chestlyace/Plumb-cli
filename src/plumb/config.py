"""The user's config.toml: model, endpoint, fallback and step limit."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH: Path = Path.home() / ".config" / "plumb" / "config.toml"

DEFAULT_CONFIG = """\
# Plumb settings. Created on first run; edit freely.

[model]
name = "gemma4:e4b"
endpoint = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
# Used automatically when the local model fails. A ":cloud" model runs on
# Ollama's servers, so your code leaves your machine only when this kicks in.
# Set to "" to never fall back.
fallback_name = "gemma4:31b-cloud"

[limits]
step_limit = 12  # model calls per turn
"""


@dataclass(frozen=True)
class Config:
    model_name: str
    endpoint: str
    fallback_name: str | None
    step_limit: int


class ConfigError(Exception):
    """config.toml is unreadable or has a bad value. The message names the fix."""


def load_config(path: Path = CONFIG_PATH) -> tuple[Config, bool]:
    """Load config.toml, creating it with defaults when missing.

    Returns the config and whether the file was just created. Keys missing
    from an existing file take their default values.
    """
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    defaults = tomllib.loads(DEFAULT_CONFIG)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path} cannot be read: {error}") from error

    model = {**defaults["model"], **data.get("model", {})}
    limits = {**defaults["limits"], **data.get("limits", {})}
    try:
        step_limit = int(limits["step_limit"])
    except TypeError, ValueError:
        raise ConfigError(
            f"{path}: [limits] step_limit must be a whole number"
        ) from None
    if step_limit < 1:
        raise ConfigError(f"{path}: [limits] step_limit must be at least 1")
    for key in ("name", "endpoint"):
        if not isinstance(model[key], str) or not model[key].strip():
            raise ConfigError(f"{path}: [model] {key} must be set")
    fallback = str(model.get("fallback_name") or "").strip() or None
    return (
        Config(
            model_name=model["name"].strip(),
            endpoint=model["endpoint"].strip(),
            fallback_name=fallback,
            step_limit=step_limit,
        ),
        created,
    )
