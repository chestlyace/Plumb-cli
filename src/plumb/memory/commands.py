"""The commands and flows that can record memory."""

from typing import Literal

# plan, review, tour and ask are flows; chat is a session that runs them.
Command = Literal["plan", "review", "tour", "ask", "chat"]
