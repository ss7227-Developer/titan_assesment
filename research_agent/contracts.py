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
    interpretation: str = Field(max_length=500, description="Wording assumption only; empty for a clear question.")
    unsupported: str = Field(max_length=500, description="Exact out-of-scope task span from the question; empty if none. Must be excluded from research_question.")
    message: str = Field(max_length=500)


class EvidenceSupport(StrictModel):
    evidence_id: int = Field(ge=1)
    observed_at: str = Field(default="", max_length=60,
                             description="For measurements, observation date/period copied from source text, not retrieval time. Empty for other kinds.")


class AssessedPart(StrictModel):
    question_part: str = Field(min_length=1, max_length=120)
    kind: Literal["fact", "papers", "comparison", "dated_measurement"]
    supports: list[EvidenceSupport] = Field(max_length=2)
    missing: str = Field(max_length=160, description="What remains unestablished; empty only if this part is supported.")
    published_after: str = Field(default="", max_length=10,
                                  description="YYYY-MM-DD cutoff for recent papers; empty when not requested.")


class PlannedSearch(StrictModel):
    tool: Literal["search_wikipedia", "search_arxiv"]
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=3, ge=1, le=3)


class ResearchAssessment(StrictModel):
    parts: list[AssessedPart] = Field(min_length=1, max_length=5)
    action: Literal["search", "finish", "limited"]
    searches: list[PlannedSearch] = Field(max_length=2)
    reason: str = Field(min_length=1, max_length=240,
                        description="Brief operational reason for the action, not private reasoning.")


class AnswerItem(StrictModel):
    text: str = Field(min_length=1, max_length=1600)
    provenance: Literal["retrieved", "inference"]
    citations: list[int] = Field(min_length=1, max_length=8)
    part_index: int = Field(default=0, ge=0, le=4, description="ZERO-based assessment part index: first part is 0.")


class Answer(StrictModel):
    items: list[AnswerItem] = Field(max_length=6)
