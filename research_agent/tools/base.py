"""Bounded HTTP access, shared tool interface and argument validation."""

import asyncio
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from ..contracts import ToolResult
from ..runtime import BudgetExceeded, Runtime

USER_AGENT = "AcademicResearchAgent/0.1 (https://github.com/ss7227-Developer/titan_assesment)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_query(query, max_results):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("query must be a nonempty string of at most 500 characters")
    if type(max_results) is not int or not 1 <= max_results <= 3:
        raise ValueError("max_results must be an integer from 1 to 3")


def tool_schema(name: str, description: str) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": {
                           "query": {"type": "string", "minLength": 1, "maxLength": 500},
                           "max_results": {"type": "integer", "minimum": 1,
                                           "maximum": 3, "default": 3}},
                       "required": ["query"]}}}


class SearchTool(Protocol):
    name: str
    schema: dict

    async def search(self, query: str, max_results: int = 3,
                     remaining_timeout: float = 8) -> ToolResult: ...


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class ProviderHTTP:
    """All external attempts (including retries) share one invocation budget."""

    def __init__(self, client: httpx.AsyncClient, runtime: Runtime):
        self.client = client
        self.runtime = runtime
        self.last_arxiv_request: float | None = None
        self.arxiv_lock = asyncio.Lock()

    async def get(self, url: str, params: dict, *, arxiv: bool = False) -> httpx.Response:
        if arxiv:
            # Hold across retries, so retries also respect the provider's spacing.
            async with self.arxiv_lock:
                return await self._get(url, params, arxiv=True)
        return await self._get(url, params)

    async def _get(self, url, params, arxiv=False):
        for attempt in range(2):
            if arxiv and self.last_arxiv_request is not None:
                await asyncio.sleep(max(0, 3 - (time.monotonic() - self.last_arxiv_request)))
            if self.runtime.http_calls >= self.runtime.max_http:
                raise BudgetExceeded("External HTTP request budget reached")
            if self.runtime.remaining("research") <= 0:
                raise BudgetExceeded("Research time budget reached")
            self.runtime.http_calls += 1
            if arxiv:
                self.last_arxiv_request = time.monotonic()
            try:
                # Stream with a size cap before parsing untrusted JSON/XML.
                async with self.client.stream("GET", url, params=params,
                                              headers={"User-Agent": USER_AGENT},
                                              timeout=min(8, self.runtime.remaining("research"))) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise ProviderError("Provider response exceeded the 2 MB limit")
                    # aiter_bytes already decoded gzip; do not decode it a second time.
                    headers = dict(response.headers)
                    headers.pop("content-encoding", None)
                    headers.pop("content-length", None)
                    result = httpx.Response(response.status_code, headers=headers,
                                            content=bytes(body), request=response.request)
                if result.status_code not in {429, 500, 502, 503, 504}:
                    if result.is_error:
                        raise ProviderError(f"Provider returned HTTP {result.status_code}")
                    return result
                delay = self.retry_delay(result.headers.get("Retry-After", "1"))
            except httpx.HTTPError:
                delay = 0.5
            if attempt == 1:
                raise ProviderError("Provider unavailable after one retry", retryable=True)
            # Never shorten Retry-After to squeeze in another request.
            if delay >= self.runtime.remaining("research"):
                raise ProviderError("Provider retry exceeds remaining research time", retryable=True)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def retry_delay(value: str) -> float:
        try:
            return max(0, float(value))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return 1.0
