"""Run: python agent.py \"Why do banks hold reserves?\""""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path


async def run(args, started):
    # LangGraph's dependencies include LangSmith; explicitly keep this CLI local.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    import httpx
    from langgraph.errors import GraphRecursionError

    from research_agent.graph import ResearchGraph
    from research_agent.model import ModelError, OllamaModel
    from research_agent.runtime import BudgetExceeded, Runtime
    from research_agent.tools import ArxivTool, WikipediaTool
    from research_agent.tools.base import ProviderHTTP
    from research_agent.trace import Trace

    runtime = Runtime(started=started)
    trace = None
    model = None
    reason, code = "runtime_error", 1
    final_answer = ""
    try:
        trace = Trace(args.trace_dir, runtime)
        trace.event("run_start", model=args.model, original_question=args.question)
        print(f"Trace: {trace.path}", file=sys.stderr)
        async with asyncio.timeout(runtime.remaining()):
            model = OllamaModel(args.model, args.ollama_url, runtime, trace)
            await model.preflight()
            async with httpx.AsyncClient(follow_redirects=False) as client:
                http = ProviderHTTP(client, runtime)
                agent = ResearchGraph(model, [WikipediaTool(http), ArxivTool(http)],
                                      runtime, trace, args.debug)
                state = await agent.run(args.question)
            reason = state["stop_reason"]
            code = 1 if reason == "invalid_output" else 0
            final_answer = state["result"]
            print(final_answer, flush=True)
    except (TimeoutError, BudgetExceeded):
        reason = "deadline_exceeded"
        final_answer = "I could not complete a validated research answer within the time/call budget."
        print(final_answer, flush=True)
        print("Research stopped at its deadline or call limit.", file=sys.stderr)
    except ModelError as exc:
        reason = "model_error"
        print(str(exc), file=sys.stderr)
    except GraphRecursionError:
        reason = "recursion_limit"
        print("Research stopped at the graph step limit.", file=sys.stderr)
    except asyncio.CancelledError:
        reason, code = "cancelled", 130
        print("Research cancelled.", file=sys.stderr)
    except ValueError as exc:
        reason, code = "runtime_error", 2
        print(f"Invalid configuration: {exc}", file=sys.stderr)
    except Exception as exc:
        # Do not print arbitrary exception strings that may contain request secrets.
        print(f"Research failed ({type(exc).__name__}). Inspect the local trace/server.", file=sys.stderr)
    finally:
        try:
            if trace:
                trace.finish(reason, code, final_answer)
        finally:
            if trace:
                trace.close()
            if model:
                try:
                    await asyncio.wait_for(model.close(), timeout=0.1)
                except (Exception, asyncio.CancelledError):
                    pass
    return code


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description="Research factual questions with Wikipedia, arXiv and local Ollama.")
    parser.add_argument("question", help="Research question, enclosed in quotes")
    parser.add_argument("--model", default="qwen3:1.7b", help="Installed local Ollama model")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--trace-dir", type=Path, default=Path(".research-agent/traces"))
    parser.add_argument("--debug", action="store_true", help="Print graph node progress to stderr")
    args = parser.parse_args()
    if not args.question.strip() or len(args.question) > 2000:
        parser.error("question must contain 1–2000 characters")
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required. Activate the documented virtual environment.")
    try:
        return asyncio.run(run(args, started))
    except ModuleNotFoundError:
        print("Dependencies are missing. Run: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
