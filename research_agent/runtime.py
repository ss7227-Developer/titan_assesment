"""Application-owned limits; models cannot write counters or deadlines."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable


class BudgetExceeded(Exception):
    pass


@dataclass
class Runtime:
    started: float = field(default_factory=time.monotonic)
    clock: Callable[[], float] = time.monotonic
    total_seconds: float = 60.0
    research_seconds: float = 40.0
    model_end_seconds: float = 58.0
    max_tools: int = 6
    max_http: int = 10
    max_models: int = 10
    tool_calls: int = 0
    http_calls: int = 0
    model_calls: int = 0
    evidence_chars: int = 0

    @property
    def elapsed(self) -> float:
        return max(0, self.clock() - self.started)

    def remaining(self, phase: str = "total") -> float:
        end = {"research": self.research_seconds, "model": self.model_end_seconds,
               "total": self.total_seconds}[phase]
        return max(0, min(end, self.total_seconds) - self.elapsed)

    def can_research(self) -> bool:
        return (self.remaining("research") > 0.1 and self.tool_calls < self.max_tools
                and self.http_calls < self.max_http
                and self.model_calls < self.max_models - 1
                and self.evidence_chars < 18000)

    def counters(self) -> dict:
        return {"tool_calls": self.tool_calls, "http_calls": self.http_calls,
                "model_calls": self.model_calls, "evidence_chars": self.evidence_chars}

    async def bounded(self, awaitable, timeout: float):
        # wait_for cancels the underlying async HTTP/model task as well.
        return await asyncio.wait_for(awaitable, timeout=max(0.001, timeout))
