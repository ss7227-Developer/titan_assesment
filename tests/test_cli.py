import asyncio
import json
import time
from argparse import Namespace

import pytest

import agent
from research_agent import graph, model, runtime


def setup_cli(monkeypatch, tmp_path, *, hang=False):
    class FakeModel:
        def __init__(self, *args):
            pass

        async def preflight(self):
            pass

        async def close(self):
            pass

    class FakeGraph:
        def __init__(self, *args):
            pass

        async def run(self, question):
            if hang:
                await asyncio.sleep(5)
            return {"result": "Retrieved evidence: A cited answer. [1]", "stop_reason": "answered"}

    monkeypatch.setattr(model, "OllamaModel", FakeModel)
    monkeypatch.setattr(graph, "ResearchGraph", FakeGraph)
    original_runtime = runtime.Runtime
    monkeypatch.setattr(runtime, "Runtime", lambda **kw: original_runtime(total_seconds=0.1 if hang else 60, **kw))
    return Namespace(question="What is a bank? api_key=private-example", model="qwen3:1.7b",
                     ollama_url="http://127.0.0.1:11434", trace_dir=tmp_path, debug=False)


@pytest.mark.parametrize("hang", [False, True])
def test_cli_trace_records_original_question_and_exact_stdout(monkeypatch, tmp_path, capsys, hang):
    args = setup_cli(monkeypatch, tmp_path, hang=hang)
    started = time.monotonic()
    code = asyncio.run(agent.run(args, started))
    output = capsys.readouterr()
    assert "Trace:" in output.err
    assert "Trace:" not in output.out
    assert code == (1 if hang else 0)
    assert time.monotonic() - started < 1
    records = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    assert records[0]["original_question"] == "What is a bank? api_key=[REDACTED]"
    assert records[-1]["final_answer"] == output.out.strip()
    assert sum(e["event"] == "run_end" for e in records) == 1
    assert records[-1]["termination_reason"] == ("deadline_exceeded" if hang else "answered")
    assert "private-example" not in json.dumps(records)
