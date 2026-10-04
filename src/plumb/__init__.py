"""Plumb: a local-first tutor for your own codebase."""

import os

# PydanticAI prints a banner on first use; the CLI's output must stay clean.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
