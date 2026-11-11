import asyncio

import pytest

from research_agent.context import MAX_MODEL_INPUT_CHARS
from research_agent.model import ModelError, OllamaModel
from research_agent.runtime import Runtime
from research_agent.trace import Trace


def test_oversized_model_input_never_reaches_the_api(tmp_path, monkeypatch):
    async def go():
        rt = Runtime()
        trace = Trace(tmp_path, rt)
        model = OllamaModel("qwen3:1.7b", "http://127.0.0.1:11434", rt, trace)

        async def forbidden(**kwargs):
            pytest.fail("Oversized context must be rejected before the network call")

        monkeypatch.setattr(model.client, "chat", forbidden)
        try:
            with pytest.raises(ModelError, match="context budget"):
                await model.chat([{"role": "user", "content": "x" * MAX_MODEL_INPUT_CHARS}], node="research")
            assert rt.model_calls == 0
        finally:
            await model.close()
            trace.close()
    asyncio.run(go())
