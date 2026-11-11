"""arXiv Atom search: metadata + abstracts only, never downloaded PDFs."""

import asyncio
import re
from urllib.parse import urlparse

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from ..contracts import Source, ToolResult
from ..runtime import BudgetExceeded
from ..trace import redact
from .base import ProviderError, ProviderHTTP, clean_text, tool_schema, utc_now, validate_query

ATOM = {"a": "http://www.w3.org/2005/Atom"}


class ArxivTool:
    name = "search_arxiv"
    schema = tool_schema(name, "Search arXiv for academic papers; return titles, authors, paper "
                         "IDs, dates, URLs and abstracts only. Use for papers, research findings "
                         "and scholarly comparisons. Supply concise keywords, not API syntax. "
                         "Abstracts do not prove peer review and are not full-text evidence.")
    endpoint = "https://export.arxiv.org/api/query"

    def __init__(self, http: ProviderHTTP):
        self.http = http

    @staticmethod
    def search_expression(query: str) -> str:
        # Avoid accidental arXiv field/operator injection; plain keyword AND search.
        words = re.findall(r"[^\W_]+(?:[-.][^\W_]+)*", query, re.UNICODE)
        if not words:
            raise ValueError("arXiv query must contain searchable words")
        return " AND ".join(f'all:"{word}"' for word in words)

    @classmethod
    def cache_key(cls, query: str, max_results: int = 3) -> str:
        # The adapter ANDs all terms, so order/repeated terms do not change retrieval.
        terms = cls.search_expression(query.casefold()).split(" AND ")
        return f"{max_results}:" + " AND ".join(sorted(set(terms)))

    async def search(self, query: str, max_results: int = 3,
                     remaining_timeout: float = 8) -> ToolResult:
        validate_query(query, max_results)
        params = {"search_query": self.search_expression(query.strip()),
                  "start": 0, "max_results": max_results,
                  "sortBy": "relevance", "sortOrder": "descending"}
        try:
            async with asyncio.timeout(remaining_timeout):
                response = await self.http.get(self.endpoint, params, arxiv=True)
                root = ElementTree.fromstring(response.content)
            if root.tag != "{http://www.w3.org/2005/Atom}feed":
                return ToolResult("error", error="arXiv did not return an Atom feed")
            sources = []
            for entry in root.findall("a:entry", ATOM):
                title = clean_text(entry.findtext("a:title", "", ATOM))
                link = entry.findtext("a:id", "", ATOM).strip()
                if title.lower() == "error" or "/api/errors" in link:
                    return ToolResult("error", error="arXiv API rejected the search")
                parsed = urlparse(link)
                if parsed.hostname not in {"arxiv.org", "www.arxiv.org"}:
                    continue
                paper_id = parsed.path.removeprefix("/abs/")
                if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", paper_id):
                    continue
                text = clean_text(entry.findtext("a:summary", "", ATOM))
                if not title or not text:
                    continue
                authors = [clean_text(a.findtext("a:name", "", ATOM))
                           for a in entry.findall("a:author", ATOM)]
                sources.append(Source(
                    provider="arxiv", source_id=paper_id, title=redact(title),
                    url=f"https://arxiv.org/abs/{paper_id}", text=redact(text[:3000]),
                    retrieved_at=utc_now(), authors=redact(authors),
                    published=entry.findtext("a:published", "", ATOM),
                    revision=entry.findtext("a:updated", "", ATOM),
                    truncated=len(text) > 3000))
            return ToolResult("ok" if sources else "empty", sources[:max_results])
        except (TimeoutError, BudgetExceeded) as exc:
            return ToolResult("error", error="arXiv search timed out or reached its budget",
                              retryable=isinstance(exc, TimeoutError))
        except ProviderError as exc:
            return ToolResult("error", error=str(exc), retryable=exc.retryable)
        except (ElementTree.ParseError, DefusedXmlException, ValueError, TypeError):
            return ToolResult("error", error="arXiv returned invalid or unsafe XML")
