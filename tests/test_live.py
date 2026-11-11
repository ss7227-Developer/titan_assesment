"""Run separately: python -m pytest -m live -o addopts='' tests/test_live.py -v"""

import asyncio
from pathlib import Path

import httpx
import pytest

from research_agent.contracts import ScopeDecision
from research_agent.model import OllamaModel
from research_agent.prompts import SCOPE_PROMPT
from research_agent.runtime import Runtime
from research_agent.tools import ArxivTool, WikipediaTool
from research_agent.tools.base import ProviderHTTP
from research_agent.trace import Trace

pytestmark = pytest.mark.live


@pytest.mark.parametrize("tool_type,query", [(WikipediaTool, "Bank"), (ArxivTool, "transformer attention")])
def test_real_provider(tool_type, query):
    async def go():
        async with httpx.AsyncClient() as client:
            return await tool_type(ProviderHTTP(client, Runtime())).search(query)
    result = asyncio.run(go())
    assert result.status == "ok", result.error
    assert result.sources and all(s.text and s.url for s in result.sources)


def test_real_ollama_refusal(tmp_path):
    async def go():
        rt = Runtime()
        trace = Trace(tmp_path, rt)
        model = OllamaModel("qwen3:1.7b", "http://127.0.0.1:11434", rt, trace)
        try:
            await model.preflight()
            response = await model.chat([
                {"role": "system", "content": SCOPE_PROMPT},
                {"role": "user", "content": "What is the best restaurant in New York City?"}],
                node="scope", schema=ScopeDecision.model_json_schema(), research=True)
            return ScopeDecision.model_validate_json(response["content"])
        finally:
            await model.close()
            trace.close()
    assert asyncio.run(go()).decision == "out_of_scope"
