"""Shared data contracts: provider adapters do not depend on the LLM or graph."""

from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class Source:
    provider: str
    source_id: str
    title: str
    url: str
    text: str
    retrieved_at: str
    authors: list[str] = field(default_factory=list)
    published: str = ""
    revision: str = ""
    truncated: bool = False
    citation_id: int | None = None

    @property
    def identity(self) -> str:
        return f"{self.provider}:{self.source_id}"


@dataclass
class ToolResult:
    status: Literal["ok", "empty", "error", "skipped"]
    sources: list[Source] = field(default_factory=list)
    error: str = ""
    retryable: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScopeDecision(StrictModel):
    decision: Literal["in_scope", "out_of_scope", "needs_clarification"]
    research_question: str = Field(max_length=2000)
    interpretation: str = Field(max_length=500)
    unsupported: str = Field(max_length=500)
    message: str = Field(max_length=500)


class AnswerItem(StrictModel):
    text: str = Field(min_length=1, max_length=1600)
    provenance: Literal["retrieved", "inference"]
    citations: list[int] = Field(min_length=1, max_length=8)


class Answer(StrictModel):
    items: list[AnswerItem] = Field(min_length=1, max_length=6)
