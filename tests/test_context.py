import json
from dataclasses import replace

from research_agent.context import MAX_CONTEXT_CHARS, make_context
from research_agent.contracts import Source
from research_agent.tools import ArxivTool, WikipediaTool


def test_provider_cache_keys_preserve_count_and_normalize_equivalent_queries():
    assert WikipediaTool.cache_key("  BANK   reserves ") == WikipediaTool.cache_key("bank reserves")
    assert WikipediaTool.cache_key("bank reserves", 1) != WikipediaTool.cache_key("bank reserves", 3)
    assert ArxivTool.cache_key("bank reserves") == ArxivTool.cache_key("reserves bank bank")
    assert ArxivTool.cache_key("bank reserves") != ArxivTool.cache_key("bank liquidity")
    assert WikipediaTool.cache_key("bank OR reserves") != WikipediaTool.cache_key("bank or reserves")


def test_large_context_is_bounded_without_replaying_evidence():
    sources = {}
    for index in range(6):
        source = Source("wikipedia", str(index), "Title " + str(index),
                        "https://en.wikipedia.org/wiki/Bank", "Unique evidence " + str(index) + " \\" * 3000,
                        "2026-11-11", citation_id=index + 1)
        sources[source.identity] = source
    state = {"question": "q" * 2000, "scope": {"research_question": "r" * 2000},
             "evidence": sources, "recent_searches": [{"query": "bank"}] * 20,
             "draft": {"items": [{"text": "bad answer" * 2000}]},
             "validation_errors": [{"path": "items.0.citations", "code": "unknown_citation"}]}
    serialized = make_context(state, repair=True)
    context = json.loads(serialized)
    assert len(serialized) <= MAX_CONTEXT_CHARS
    assert len(context["recent_searches"]) == 4
    assert len(context["evidence"]) == 6
    assert all(s["truncated"] for s in context["evidence"])
    assert context["rejected_answer"]["truncated"]


def test_each_source_text_appears_once_in_context():
    text = "A bank is a financial institution."
    source = Source("wikipedia", "1", "Bank", "https://en.wikipedia.org/wiki/Bank", text, "2026-11-11", citation_id=1)
    serialized = make_context({"question": "What is a bank?", "scope": {"research_question": "What is a bank?"},
                               "evidence": {source.identity: source}})
    assert serialized.count(text) == 1
