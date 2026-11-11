"""Wikipedia search + actual plaintext article introductions, in one API call."""

import asyncio
from urllib.parse import quote

from ..contracts import Source, ToolResult
from ..runtime import BudgetExceeded
from ..trace import redact
from .base import ProviderError, ProviderHTTP, clean_text, tool_schema, utc_now, validate_query


class WikipediaTool:
    name = "search_wikipedia"
    schema = tool_schema(name, "Search English Wikipedia and return article introduction extracts. "
                         "Use for definitions, established explanations, banking, economics, "
                         "history and background. Returns evidence and canonical article URLs, "
                         "not full articles or current legal verification. Use short topic keywords.")
    endpoint = "https://en.wikipedia.org/w/api.php"

    def __init__(self, http: ProviderHTTP):
        self.http = http

    async def search(self, query: str, max_results: int = 3,
                     remaining_timeout: float = 8) -> ToolResult:
        validate_query(query, max_results)
        params = {"action": "query", "format": "json", "formatversion": 2,
                  "generator": "search", "gsrsearch": query.strip(),
                  "gsrnamespace": 0, "gsrlimit": max_results,
                  "prop": "extracts|info|revisions|pageprops", "inprop": "url",
                  "exintro": 1, "explaintext": 1, "exlimit": max_results,
                  "exchars": 3000, "rvprop": "ids", "ppprop": "disambiguation"}
        try:
            async with asyncio.timeout(remaining_timeout):
                response = await self.http.get(self.endpoint, params)
                data = response.json()
            if "error" in data:
                return ToolResult("error", error="Wikipedia API rejected the search")
            pages = data.get("query", {}).get("pages", [])
            sources = []
            for page in sorted(pages, key=lambda p: p.get("index", 999)):
                if "disambiguation" in page.get("pageprops", {}) or "missing" in page:
                    continue
                text = clean_text(page.get("extract", ""))
                if not text:
                    continue
                title = page["title"]
                # Build canonical links ourselves; never let provider text supply an arbitrary host.
                url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()_")
                revisions = page.get("revisions", [])
                sources.append(Source(
                    provider="wikipedia", source_id=str(page["pageid"]),
                    title=redact(title), url=url, text=redact(text[:3000]),
                    retrieved_at=utc_now(),
                    revision=str(revisions[0].get("revid", "")) if revisions else "",
                    truncated=len(text) >= 3000 or text.endswith("…")))
            return ToolResult("ok" if sources else "empty", sources[:max_results])
        except (TimeoutError, BudgetExceeded) as exc:
            return ToolResult("error", error="Wikipedia search timed out or reached its budget",
                              retryable=isinstance(exc, TimeoutError))
        except ProviderError as exc:
            return ToolResult("error", error=str(exc), retryable=exc.retryable)
        except (ValueError, KeyError, TypeError, AttributeError):
            return ToolResult("error", error="Wikipedia returned an invalid response")
