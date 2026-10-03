"""Where the user's config.toml lives."""

from pathlib import Path

CONFIG_PATH: Path = Path.home() / ".config" / "plumb" / "config.toml"
