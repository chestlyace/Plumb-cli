"""What the model returns for questions, and what the learner answers."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from plumb.memory.mastery import ConceptSlug


class Option(BaseModel):
    label: str = Field(description="A short name for this approach.")
    explanation: str = Field(
        description="One or two sentences on its trade-off in her code."
    )


class Question(BaseModel):
    concept: ConceptSlug = Field(
        description="Kebab-case slug, e.g. session-per-request."
    )
    concept_name: str = Field(description="Readable name, e.g. Session per request.")
    question: str
    citation: str = Field(
        description="The file and lines it is about, e.g. app/db.py:12-30."
    )
    options: list[Option] = Field(min_length=2, max_length=3)

    @field_validator("concept", mode="before")
    @classmethod
    def _slug(cls, value: object) -> object:
        """Small models write "Session Per Request"; turn that into a slug."""
        if isinstance(value, str):
            return "-".join(
                "".join(c if c.isalnum() else " " for c in value.lower()).split()
            )
        return value


class Questions(BaseModel):
    questions: list[Question] = Field(min_length=2, max_length=3)


class CheckQuestion(BaseModel):
    question: str
    options: list[str] = Field(min_length=2, max_length=3)
    correct: int = Field(description="Number of the correct option, starting at 1.")
    why: str = Field(description="One or two sentences on why that option is right.")

    @model_validator(mode="after")
    def _correct_in_range(self) -> CheckQuestion:
        if not 1 <= self.correct <= len(self.options):
            raise ValueError(f"correct must be between 1 and {len(self.options)}")
        return self


@dataclass(frozen=True)
class Answer:
    kind: Literal["option", "dont_understand", "skip", "skip_rest"]
    option: int | None = None  # 1-based, when kind == "option"
