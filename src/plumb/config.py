"""The user's config.toml: model, endpoint, fallback and step limit."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH: Path = Path.home() / ".config" / "plumb" / "config.toml"

DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_ENDPOINT = "http://localhost:11434/v1"
DEFAULT_FALLBACK = "gemma4:31b-cloud"
DEFAULT_STEP_LIMIT = 12


def config_text(
    model: str = DEFAULT_MODEL,
    fallback: str | None = DEFAULT_FALLBACK,
    endpoint: str = DEFAULT_ENDPOINT,
    step_limit: int = DEFAULT_STEP_LIMIT,
) -> str:
    """config.toml with these values. `tutor setup` writes it again."""
    return f"""\
# Plumb settings. `tutor setup` rewrites this file; you can also edit it.

[model]
name = "{model}"
endpoint = "{endpoint}"  # Ollama's OpenAI-compatible endpoint
# Used automatically when the local model fails. A ":cloud" model runs on
# Ollama's servers, so your code leaves your machine only when this kicks in.
# Set to "" to never fall back.
fallback_name = "{fallback or ""}"

[limits]
step_limit = {step_limit}  # model calls per turn
"""


DEFAULT_CONFIG = config_text()


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
