# Local academic research agent

A Python CLI using **one local Ollama model**, **LangGraph orchestration**, and two
API tools: **Wikipedia article extracts** and **arXiv paper metadata/abstracts**.
It researches factual questions, prints answers with verifiable references, and
labels retrieved information separately from model inference. No paid API keys,
FRED integration, general web search, or PDF downloads are required or implemented.

**Prototype status:** the workflow and safeguards are implemented, but complete,
correct answers are not guaranteed. The latest eight-question run scored **1 pass,
3 partial, 4 fail**; a safe limitation is not counted as a complete answer.
See [the measured results](evaluation/LATEST_SUITE_REPORT.md).

## 1. Prerequisites

- Git and Python **3.11 or newer**; **Python 3.12 is recommended** and 3.12.14 was
  used for testing. Check with `git --version` and `python3.12 --version`.
- [Ollama installed](https://ollama.com/download), with its local server running.
- Internet access to download dependencies/models initially and to query Wikipedia
  and arXiv during research. Model inference is local; searches are not offline.
- Free disk space for Python dependencies and the selected model. The tested model
  files are approximately **3.39 GB for `qwen3.5:4b`** and **1.36 GB for
  `qwen3:1.7b`**. These are download/storage sizes, not runtime RAM requirements;
  runtime memory and performance depend on your hardware.

No `.env` file, database, API key, or remote tracing account is needed. Do not put
secrets in research questions: queries are sent to the selected public API.

## 2. Clone and install dependencies

Run from a terminal. Keep subsequent agent/test commands in the repository root.

### macOS / Linux

```bash
# Replace python3.12 with your installed Python 3.11+ interpreter if necessary.
git clone https://github.com/ss7227-Developer/titan_assesment.git
cd titan_assesment
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
git clone https://github.com/ss7227-Developer/titan_assesment.git
cd titan_assesment
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` instead of
`python` in the install/run/test commands; changing your execution policy is not
required. On macOS/Linux, `.venv/bin/python` also works without activation.

`requirements.txt` pins the resolved dependencies, including LangGraph, the Ollama
Python SDK, HTTPX, defusedxml, and pytest. `requirements.in` lists the direct
requirements. Optional uv equivalent: `uv venv --python 3.12`, followed by
`uv pip sync requirements.txt`.

## 3. Start Ollama and download a model

In a **separate terminal**, start the server if the Ollama desktop app/service is
not already running:

```bash
ollama serve
```

Leave that terminal open. If the server reports that port 11434 is already in use,
an Ollama instance may already be running; do not start a second instance.

Back in the project terminal, download the recommended tester model once:

```bash
ollama pull qwen3.5:4b
ollama list
```

The existing CLI default is the smaller `qwen3:1.7b`. To use the command without a
model flag, download that model too:

```bash
ollama pull qwen3:1.7b
```

The agent checks that the selected model exists and advertises tool support. It
never downloads a model automatically. A new clone does **not** include `.venv`,
model files, caches, or prior local execution traces.

## 4. Run the agent

With the project environment active and Ollama running:

```bash
# Recommended explicit tester configuration:
python agent.py "Why do banks hold reserves?" --model qwen3.5:4b

# Uses qwen3:1.7b; requires that model to be installed:
python agent.py "Why does yeast make bread rise?"

# Academic paper search:
python agent.py "Find recent papers on machine learning for credit risk assessment." --model qwen3.5:4b

# Out-of-scope request: should refuse subjective recommendations.
python agent.py "What is the best restaurant in New York City?" --model qwen3.5:4b
```

Enclose the **entire question in quotes**. Each invocation handles one English
question; there is no chat history between invocations. Questions must contain
1–2,000 characters. Run `python agent.py --help` for the CLI help.

| Option | Default | Purpose |
| --- | --- | --- |
| `--model MODEL` | `qwen3:1.7b` | Choose an installed tool-capable local model. |
| `--ollama-url URL` | `http://127.0.0.1:11434` | Local Ollama address; only loopback hosts are accepted. |
| `--trace-dir PATH` | `.research-agent/traces` | Writable directory for local JSONL traces. |
| `--debug` | Off | Print graph node progress to stderr. |

The answer, refusal, clarification, or evidence limitation goes to **stdout**.
The trace path, debug progress, and runtime diagnostics go to **stderr**:

```bash
python agent.py "Explain yield curve inversions." --model qwen3.5:4b > answer.txt 2> diagnostics.txt
```

Exit codes: **0** for a completed response, including refusal/clarification or
insufficient evidence; **1** for a runtime, deadline, or invalid-answer failure;
**2** for invalid invocation/configuration; **130** for cancellation. Exit 0 does
not establish that the answer is substantively correct or complete.

## 5. Tool wrappers and agent-loop wiring

**Both API wrappers are implemented and wired into the loop.** They are independent
modules with the same search interface:

```text
search(query, max_results=3, remaining_timeout=8) -> ToolResult
```

| Tool | API and returned evidence |
| --- | --- |
| `search_wikipedia` | English MediaWiki API; ranked article search plus plaintext introduction extracts, article/page IDs, revision IDs, titles, canonical URLs, and retrieval time. |
| `search_arxiv` | arXiv Atom API; paper titles, versioned paper IDs, authors, publication dates, canonical abstract URLs, and abstracts. No PDFs/full papers are read. |

Each wrapper exposes a tool description/schema and cache key. Both return the
same normalized status/source/error contract (`ok`, `empty`, `error`, or skipped
execution reported by dispatch). Provider errors are observations for the agent,
not fabricated evidence. Shared HTTP handling bounds response sizes, requests,
retries, and arXiv pacing.

The CLI constructs both wrappers with a shared budgeted HTTP client and registers
them in the LangGraph research graph. The LLM chooses tools/queries; the tool node
validates and executes those requests, merges evidence, and returns observations
to the next research decision. Neither provider is forced on every question.
Relevant implementation links, if desired: [Wikipedia](research_agent/tools/wikipedia.py),
[arXiv](research_agent/tools/arxiv.py), and [graph](research_agent/graph.py).

## 6. Orchestration: ReAct with LangGraph

```text
scope -> research <-> tools -> synthesize -> validate -> render -> END
```

This is **one research agent**, not a collection of independently prompted agents.
LangGraph provides explicit state, nodes, and conditional transitions. We use a
**ReAct-style reasoning/acting loop** because searches can expose new evidence or
missing details that should change the next search. The model chooses the initial
native tool call, then structured assessments/follow-up searches. This avoids a
fixed upfront search sequence while keeping execution limits in application code.
See [ReAct](https://arxiv.org/abs/2210.03629) and
[LangGraph's workflow/agent overview](https://docs.langchain.com/oss/python/langgraph/workflows-agents).

Before finishing, a structured completion assessment records question parts,
supporting evidence IDs, remaining gaps, a short action reason, and `search`,
`finish`, or `limited`. The agent may search again or stop honestly with limitations.
Paper evidence, comparisons, observation periods, and declared publication cutoffs
have general checks; there are no hard-coded answers to the evaluation questions.
One assessment repair is allowed per run. If repair fails, only mechanically valid
partial mappings can be retained, with completeness explicitly unverified.

Separate LLM synthesis receives the assessment and only its supported sources.
Instructions require it to directly answer supported parts, omit tangential/repeated
claims, and identify gaps instead of filling them from model memory. The renderer
then prints the answer and references. Hidden reasoning is not requested or logged.

## 7. Answers, citations, and inference labels

Every substantive generated answer item has a provenance label and inline numeric
citation(s):

- **`Retrieved evidence:`** a faithful paraphrase of information actually in the
  returned extract/abstract. It must not introduce unrelated model-memory facts.
- **`Model inference:`** a deduction based on cited retrieved premises, rather than
  a conclusion explicitly reported by the source. This label does not justify
  invented measurements, unsupported facts, or speculative timelines presented as known.
- **`Not established from retrieved evidence:` / `Research limitation:`** explicitly
  identify unanswered portions and why the agent stopped. A limitation is not a
  successful full answer.

Example of the output format, shortened from a captured yield-curve run:

```text
Retrieved evidence: A sustained inversion of the yield curve has historically
preceded U.S. recessions and is used in simple recession-probability models. [1]

Sources:
[1] Yield curve — Wikipedia article extract; retrieved 2026-11-11; revision 1379075602
    https://en.wikipedia.org/wiki/Yield_curve
```

The numbered reference list includes enough metadata to locate each source:

- **Wikipedia:** article title, source type (`Wikipedia article extract`), URL,
  retrieval date, and revision when available.
- **arXiv:** paper title, source type (`arXiv paper abstract`), paper ID including
  version, URL, publication year, and authors when available.

For example, a captured paper reference is:

```text
How Data Quality Affects Machine Learning Models for Credit Risk Assessment
— arXiv paper abstract; ID 2511.10964v1; 2025; Andrea Maurino
https://arxiv.org/abs/2511.10964v1
```

References are generated from retrieved metadata, not model-invented URLs. Open the
link to verify the title/ID, and inspect the trace's returned text to check what the
agent actually read. A linked full article may contain material outside the
introduction/abstract returned by the tool.

Code checks citation existence, assessed-part membership, selected date requirements,
and whether reported numeric literals occur in cited text/paper metadata. One
answer repair is allowed within remaining budgets. **These are mechanical checks,
not proof that every claim is entailed by its citation.** Directness, factual
support, source relevance, and inference quality still require evaluation; current
models can produce incomplete or misleading answers despite valid reference IDs.

## 8. Scope, budgets, and local traces

Supported scope includes factual banking, economics, history, regulatory history,
baking science, and academic papers. Subjective recommendations, personal advice,
social chat, and unrelated creative requests are outside scope. Harmless ambiguity
uses a reasonable interpretation; material ambiguity asks for clarification. Mixed
requests should answer the supported portion and state the scope limitation.

There is no FRED/current-data API. Without dated observations, the agent should
state that a current economic value/change cannot be verified and provide supported
context if available. It must not guess a current rate. Wikipedia background alone
cannot establish recent paper findings or detailed/current legal obligations.

- Total invocation deadline: **60 seconds**, including model loading and searches.
  Research ends by 40 seconds; model work by 58 seconds, reserving output time.
- Maximum 6 tool executions, 10 provider HTTP attempts, 10 model calls, and 3 results
  per search. Oversized positive native requests are capped at 3 and traced;
  invalid types/nonpositive values still fail.
- The model decides when evidence is sufficient. There is no two-successful-search
  cap. Two consecutive attempts adding no new unique sources stop research; a new
  source resets the counter. Successful/empty searches are cached per invocation.
- Unique stored evidence: 18,000 characters; individual excerpts: 3,000 characters.
  Model snapshots are bounded, with marked truncation; character limits are not
  exact tokenizer counts.

Each run writes `.research-agent/traces/<run-id>.jsonl`, ignored by Git. JSON events
include the original question, node transitions, tool names/arguments/results,
requested/effective result counts, cache hits, accepted/rejected assessments,
timing/counters, stopping reason, and final answer. Files stay local; recognized
credential patterns are redacted. No remote tracing account is required. LangSmith
is a transitive dependency of LangGraph, but this CLI disables its remote tracing.
`prompt-log.md` separately records the development conversation.

## 9. Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing dependencies | Activate the environment, then run `python -m pip install -r requirements.txt`. |
| Wrong Python / syntax errors | Use Python 3.11+; the system `python3` may be older. Check `python --version` after activation. |
| Cannot reach Ollama | Start the desktop service or `ollama serve`; check the host with `--ollama-url`. |
| Missing model | Run `ollama pull` with exactly the tag selected by `--model`; confirm with `ollama list`. |
| Model lacks tools | Select an installed model advertising tool support; `gemma3:270m` is not suitable. |
| Trace directory cannot be written | Run from a writable project directory or pass `--trace-dir` with a writable path. |
| Empty/provider-error results | Check network access and diagnostics/traces. Sources may lack coverage or be unavailable. |
| Deadline/assessment limitation | Inspect the stated gaps and trace; try a narrower question. A larger model is not guaranteed to fix retrieval. |

## 10. Tests, reproducible evaluation, and actual results

```bash
# Offline: no server/network required; network connections are blocked by fixtures.
python -m pytest -q

# Explicit live integration checks: Ollama + installed default model + internet.
python -m pytest -m live -o addopts='' tests/test_live.py -v

# Real eight-question CLI suite with the recommended tester:
python evaluation/run_questions.py --model qwen3.5:4b

# Optional subset:
python evaluation/run_questions.py --ids 3 6 --model qwen3.5:4b
```

The live pytest model check uses `qwen3:1.7b`, so install that tag before running it.
The evaluation runner respects `--model` and saves stdout, stderr, copied JSONL
traces, and counters under a new `.research-agent/evaluations/` directory. Outputs
are reviewed manually; normal exit status is not an answer-quality grade.

**91 offline tests passed.** The latest full 4B suite completed in about **46
seconds** total: **1 pass, 3 partial, 4 fail**, versus the original baseline's 1 pass,
2 partial, 5 fail. Safer limits improved, but complete-answer quality remains weak.
Paper retrieval varied across runs; one successful spot check did not reproduce
reliably. The yield-curve explanation was supported, but recent papers were missing.

The preferred tester is 4B because it demonstrated a useful supported paper summary
in an earlier subset test. It is slower/larger than 1.7B, and the installed 8B
alternative did not improve the two tested cases. The default remains 1.7B for
reproducibility. None is established as reliably answering the whole suite.

Reports and actual outputs:

- [Latest full suite](evaluation/LATEST_SUITE_REPORT.md) / [answers](evaluation/latest-suite-answers.md)
- [Original baseline](evaluation/REPORT.md) / [answers](evaluation/answers.md)
- [Assessment before/after comparison](evaluation/ASSESSMENT_COMPARISON.md)
- [Installed-model comparison](evaluation/MODEL_COMPARISON.md)
- [Final quick-fix check](evaluation/QUICK_FIX_CHECK.md)

With more time: improve query reformulation and evidence-type classification,
retrieve bounded relevant Wikipedia sections, evaluate semantic claim support on
held-out questions, and run repeated full-suite/model comparisons. Preserve citation
safeguards and honest gaps throughout. Exact token budgeting and HTTP client/timeout
cleanup remain engineering follow-ups.
