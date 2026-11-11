# Academic research agent — architecture draft

Status: v1 implemented. The architecture remains one LangGraph research agent with separate synthesis, Wikipedia/arXiv tools, and local Ollama. Improvements include a per-invocation cache, a stop after two consecutive attempts add no sources, bounded model snapshots, targeted citation repair, and question/answer traces. Successful search count does not impose a hard stop. No additional source providers were added.

## Goal and v1 scope

Build a Python CLI that accepts one academic research question, uses one research agent backed by a local Ollama model, and prints a grounded answer with citations to stdout. The model chooses Wikipedia or arXiv, examines returned evidence, and decides whether another search is useful.

Scope means factual research and educational explanation, including banking, economics, history, regulatory history, baking science, and academic papers. It is not restricted to paper discovery. Classify by the intent of the question, not a subject blacklist: factual historical/economic research is supported even when people or institutions are involved. Subjective recommendations, personal advice, social chat, and unrelated creative tasks are out of scope. “What is the best restaurant in New York City?” is refused. Distinguish scope refusal from insufficient retrieved evidence.

For harmless ambiguity, use a reasonable interpretation internally and proceed; do not append raw scope-stage prose to the researched answer. Ask a focused clarification when an unknown detail materially changes the result. For mixed requests, answer the supported part and add a code-generated scope limitation; refuse the entire request only when no supported research part remains. Any factual answer claims must pass through synthesis and citation validation.

Confirmed evidence depth for v1: Wikipedia extracts and arXiv metadata/abstracts; no paper PDF parsing. Answers must not imply that full articles or papers were read. Proposed remaining boundaries: English, one question per invocation, no conversation memory, no web UI, no database/vector store, and no general web search. Only model inference is local; searches require external network requests.

## Architecture and control flow

```text
CLI invocation: start 60-second deadline → preflight → LangGraph StateGraph
  START → scope
    ├─ out_of_scope / needs_clarification → render → END
    └─ in_scope → research
                    ├─ tool calls + budget → tools → research
                    └─ ready / research limit → synthesize → validate
                                                           ├─ pass → render → END
                                                           ├─ repair allowed → synthesize
                                                           └─ fail → render limitation → END
  Any node: fatal failure / overall deadline → terminal error → END
```

LangGraph owns orchestration, shared state, and transitions; there is no separate hand-written Python controller loop or second coach agent. One research agent uses the same local Ollama model for scope classification, research decisions, and synthesis. Nodes are steps of that agent, not additional agents. Application code inside nodes and conditional edges enforces timeouts, budgets, allowed tools, and citation checks. The LLM chooses search queries, tools, interpretation of results, and when it has enough evidence; enforcement can stop a run but does not prescribe a fixed search sequence.

### Graph state

Use a typed state schema with these groups:

| State | Contents |
| --- | --- |
| Input/config | Question, model name, immutable configured limits. |
| Research context | Pending tool calls, a per-invocation query/result cache, and the last four compact search observations. Rebuild a fresh bounded model snapshot from the question, unique evidence registry, recent observations, and remaining budget; do not replay full tool responses. |
| Scope | Decision (`in_scope`, `out_of_scope`, `needs_clarification`), supported research question, unsupported portions, stated interpretation, and brief user-facing explanation. Mixed requests with a supported part route as in_scope. |
| Evidence | Registry keyed by source identity, run-local citation IDs, normalized excerpts/abstracts and metadata, deduplicated query history. |
| Execution | Monotonic start/deadline, tool/HTTP/model counts, remaining research allowance, repair count. Counters and deadlines are written only by application nodes. |
| Output | Structured answer items, citation validation errors, final rendered response, status and stop reason. |

State updates are returned by nodes. Execution is sequential: merge evidence/cache, replace the bounded search observations and scalar decisions/status, and increment counters in code. No checkpoint database or external tracing service is required. Local JSON traces record node/state transitions and execution events; optional `--debug` also prints brief progress to stderr. Trace records are for inspection, not resumable graph checkpoints.

### Node responsibilities and transitions

1. **Preflight (CLI):** start timing before setup, validate input/config, open the local trace, and check Ollama availability, model presence and tool support within the deadline. Do not silently download models.
2. **`scope`:** model classifies the question, records any stated interpretation, and separates supported/unsupported parts. Proceed with the supported part of mixed requests. Refusal/material clarification goes directly to rendering with no external searches. Malformed decisions get at most one repair within budgets, then fail clearly.
3. **`research`:** model sees the question, tool schemas, evidence, as-of date, and remaining allowance. An initial native tool call starts retrieval. Before completion, a structured assessment maps question parts to supporting IDs and gaps, choosing a focused follow-up, complete finish, or explicitly limited finish. Plain text without tool calls is never treated as readiness. General paper/provider, comparison coverage, observation-period, and paper-recency metadata checks apply. One assessment repair per run is allowed; an inconsistent result falls back to mechanically valid partial mappings with an explicit limitation. All attempts consume existing budgets. No new graph node or agent is introduced.
4. **`tools`:** validate arguments, reuse cached successful/empty results, and execute uncached calls sequentially within limits. Do not cache transient errors. Record every call, including cache hits/skips, and merge unique evidence. Stop after two consecutive attempts add no sources, resetting this counter whenever a new source is found. Successful searches can continue within existing budgets until the model decides evidence is sufficient. Route to separate synthesis/insufficient evidence when stopping.
5. **`synthesize`:** same model, tools disabled, sees the completion assessment and only sources mapped as supported. Each item identifies its assessed part and may cite only that part's support IDs. Missing parts must not be filled from memory; the renderer adds gaps and limitations. Keep a model-call/time reserve for this phase. Citation repair includes the rejected draft and specific errors; no new searches during synthesis.
6. **`validate`:** code checks structure, nonblank text, provenance labels, citation IDs, and required references. One repair receives the rejected answer plus specific item paths, unknown IDs, and allowed IDs within the context/time budgets. Oversized rejected drafts have an explicitly truncated representation. Otherwise render a limitation response.
7. **`render`:** print the validated synthesis, references built from registry metadata, and any code-generated limitation. Never append the scope model's interpretation or unsupported-task prose as answer content. Refusals use fixed code text; clarification requests remain explicitly labeled questions. Record termination and route to `END`.

### Stopping conditions

- Normal: model declares sufficient evidence, synthesis passes validation, then `END`.
- Scope: refusal or clarification after `scope`, then `END`.
- Research exhaustion: tool/HTTP/context/research-model allowance exhausted; attempt evidence-backed synthesis with a visible limitation, or report insufficient evidence if nothing useful was retrieved. Do not force both tools or silently switch to model-memory answers.
- Invalid output: one unsuccessful citation/format repair, then a limitation response and `END`.
- Hard stop: overall deadline, exhausted total model allowance, unrecoverable Ollama failure, or cancellation. Stop scheduling work; cancel in-flight calls and emit the appropriate runtime failure. Hard deadlines never trigger an additional LLM call.
- Safety backstop: proposed LangGraph recursion limit of 40 super-steps, independent of tool/model budgets. Catch limit exhaustion as a runtime failure; normal termination must come from explicit routing rather than deliberately hitting this limit.

## Components and interfaces

Use a small Python package with a CLI entry point, LangGraph graph/state definitions, agent/model adapter, shared contracts, output renderer, and a `tools` package. Keep Wikipedia and arXiv in separate modules. Use LangGraph directly rather than a prebuilt multi-agent supervisor; the Ollama adapter may use its SDK without adding another orchestration layer.

| Component | Responsibility / minimum contract |
| --- | --- |
| CLI | Single command: `python agent.py "question"`, defaulting to `qwen3:1.7b`; optional `--model MODEL`, `--trace-dir PATH`, and `--debug`. Final result on stdout; diagnostics and trace location on stderr. |
| LangGraph graph | Own shared state and explicit/conditional edges; nodes enforce deadlines, counters, dispatch, validation, and termination. The model cannot alter limits. |
| Ollama adapter | Translate message history, tool definitions, tool calls, and tool responses to/from the local chat API. |
| Tool module | Expose name, description, argument schema, and `search(query, max_results, remaining_timeout) → ToolResult`. |
| ToolResult | Status (`ok`, `empty`, `error`), normalized source records, and an optional sanitized error with retryability. |
| Source record | Run-local citation ID, provider source ID, source type, title, canonical URL, retrieved text, retrieval time; authors/date/version when available. |
| Answer renderer | Render direct answer items labeled `Retrieved evidence` or `Model inference`, inline `[1]` citations, and a numbered references list with source type, title and clickable URL. Include arXiv ID/version, authors/year when available, and abstract-only evidence. Wikipedia references identify article/extract and retrieval date/revision if available. |

Each tool returns bounded useful text, not only links. Wikipedia search should retrieve a bounded article introduction/extract for selected hits. arXiv search returns abstracts and metadata. Any additional HTTP requests inside a tool remain subject to the graph execution deadline and request budget.

Configuration includes default/override model name, local Ollama URL, tool result limit, model/tool timeouts, and call budgets. The overall v1 deadline is 60 seconds; configuration may tighten but not extend it. No model-generated code execution, arbitrary URL fetching, or unrestricted tools.

## Answer provenance and Wikipedia-only guidance

Directly address the question first. Internally each answer item has text, `retrieved` or `inference` provenance, and supporting citation IDs; every substantive item requires at least one retrieved source. Rendering preserves these labels per item, so mixed provenance cannot hide in a single unlabeled paragraph. Omit an inference section when none is needed.

- **Retrieved evidence:** faithful paraphrases of facts explicitly supported by the returned extract/abstract. Quotation marks are reserved for actual source text.
- **Model inference:** a clearly labeled deduction or connection based on cited retrieved premises, not an extra unsupported factual claim from model memory. It must not be presented as the source's conclusion. If support is absent, omit the claim or state that the tools did not establish it.
- Code can validate provenance labels and citation existence, but cannot prove entailment. Add model-quality evaluations that compare statements to the actual retrieved text; do not call citation validation a factual accuracy guarantee.

Recommended policy: Wikipedia-only answers are acceptable for stable background, definitions, established mechanisms, and historical/economic overviews **when the retrieved extracts support the question's specific claims**. For example, a supported explanation of yeast fermentation can use Wikipedia alone; arXiv need not be searched merely to satisfy a quota.

Wikipedia-only evidence is inadequate for requests for recent papers, state-of-the-art claims, detailed study results, or strong/disputed causal conclusions. Use arXiv when relevant papers are available; otherwise explain the coverage gap. Historical descriptions of regulation can be answered from supporting extracts, but v1 cannot verify current compliance obligations or offer authoritative legal guidance with these two sources. Conflicting evidence must be disclosed rather than resolved by assertion. arXiv abstracts are also limited evidence and do not establish peer-review status.

## Initial local model

Confirmed model: `qwen3:1.7b`, with thinking disabled where supported, configurable through `--model`. The model was downloaded with approval and real tool, scope, and CLI checks have been run. Broader grounding quality still requires evaluation; do not infer quality from API connectivity alone.

## Local JSON traces

Write one local append-only JSON Lines (`.jsonl`) file per invocation under `.research-agent/traces/`, with a unique run ID; each line is an independently parseable JSON event. Allow `--trace-dir` to override the location. Exclude generated traces from Git. This is separate from the development conversation in `prompt-log.md`.

- Common event fields: schema version, run ID, increasing sequence number, UTC timestamp, monotonic elapsed milliseconds, event type, node name, and current status/counters.
- Events: run start with the original question, node start/end, transitions, model input size/timing/status, tool start/end (including cache hits and new source IDs), validation result, research stopping reason, and run end with the final stdout answer. Redact recognized secrets in question/answer as in all trace fields.
- Tool events include tool name, validated arguments, call ID, normalized returned results exactly as supplied to the model, elapsed duration, success/empty/error/skipped status, and sanitized error details. Mark any source truncation. Do not log raw HTTP bodies in addition to the normalized result.
- Decision events contain a short operational summary such as “search arXiv for paper evidence” or “enough evidence to answer,” plus the selected action. Never store hidden reasoning, thinking tokens, or chain-of-thought. Full model conversation dumps are unnecessary.
- Final event includes termination reason (`answered`, `partial_answer`, `out_of_scope`, `clarification_needed`, `insufficient_evidence`, `deadline_exceeded`, `model_error`, `invalid_output`, `recursion_limit`, `cancelled`, or `runtime_error`), total timing, counters, and exit status. Emit a terminal event on handled failure/cancellation; abrupt process termination may leave an incomplete trace.
- Traces may contain research queries and retrieved text; redact credentials/secrets before writing and avoid logging environment/header values. Keep files local, with no paid keys or remote tracing. Default to a trace for every invocation; if the trace cannot be opened, fail preflight clearly rather than claiming observability was recorded.

## Reliability defaults to review

- Stop after 2 consecutive attempts with no new sources; reset the counter when a source is added. There is no fixed successful-search cap: the model decides when evidence is sufficient within backstops of 6 actual tool executions, 10 external HTTP attempts including retries, and 10 total model calls; 3 results/search. Cache keys include provider, normalized query, and result limit; arXiv AND-term ordering is normalized. Cache lifetime is one invocation, with no cross-run persistence.
- Hard user-visible deadline: **60 seconds from CLI entry**, including preflight, cold model loading, scope, searches, rate-limit waits, synthesis, validation, trace writes, and output. Stop research by elapsed 40 seconds; reserve up to 18 seconds for synthesis/optional repair, and the final 2 seconds for deterministic validation/rendering/cleanup. All model work stops by elapsed 58 seconds. Limit each tool execution to 8 seconds and each model request to 18 seconds, further reduced by the remaining phase and overall deadline. Never start a retry or repair without time remaining.
- Use cancellable calls and an outer invocation deadline, not just socket inactivity timeouts or a LangGraph recursion limit. If a complete validated answer cannot be produced in time, emit a bounded limitation/timeout outcome rather than an unvalidated or fabricated answer. A researched answer on every question cannot be guaranteed within one minute; termination and a user-visible outcome are required within that budget under normal local scheduling. Ctrl-C exits cleanly. Report cold and warm timings separately in live verification.
- Permit one transient HTTP retry within existing budgets; respect provider rate limits and Retry-After. arXiv requests are serialized with at least 3 seconds between requests, per its API terms. Wikipedia uses an identifying User-Agent.
- Reject unknown tools/malformed arguments without executing them. Equivalent cached searches return cached results without provider requests. Feed compact status/citation-ID observations to the next bounded model snapshot.
- Cap source text at 3,000 characters and stored unique evidence at 18,000 characters. Bound serialized research/synthesis context to 16,000 characters by trimming excerpts and metadata, with truncation flags; guard the complete model input (messages/tools/schema) at 24,000 characters. These are character budgets, not exact tokenizer counts. Evidence appears once per snapshot; recent search observations contain IDs/status rather than repeated full text.
- On a single-provider failure, allow the model to use the other source. Empty results are not an infrastructure failure. If all useful evidence is missing, print an explicit insufficient-evidence response. If Ollama cannot produce a final response, print an actionable error to stderr and exit nonzero.
- Exit codes: 0 for a completed/partial answer, scope refusal, clarification request, or insufficient-evidence result; 1 for runtime failures including hard deadline exhaustion; 2 for invalid invocation/configuration. On hard timeout, print a short limitation to stdout, diagnostic to stderr, and a terminal trace event without another model call. Local JSON traces are the only persistent application run logs in v1.

## Setup and verification

Setup: Python 3.11+ (tested with 3.12.14) in a virtual environment, `python agent.py "question"`, LangGraph, Ollama SDK, HTTPX, defusedxml, and pytest. `requirements.txt` pins dependencies. No paid keys or hosted fallback. LangSmith is a transitive dependency of LangGraph; runtime remote tracing is explicitly disabled. See README.md for setup, running, traces, and separate offline/live checks.

Use **pytest for offline tests** with fake model responses, fake clocks, and mocked HTTP fixtures. Default tests must not contact Ollama or external providers. Live verification is a separate explicit command/suite, excluded from the default test run: check real local Ollama tool calls/structured responses and each real provider API, then run end-to-end questions and record latency and trace artifacts. Do not claim live integrations were verified from offline tests.

## Remaining gaps and implementation checks

No blocking architecture questions remain under the defaults above. Remaining work is verification and concrete implementation setup, not another scope/model decision:

1. **Local feasibility:** verify that the chosen model is installed, tools/structured output work, and useful cited answers fit the 60-second deadline on this machine. A timeout response is the fallback, not permission to exceed the deadline or silently change models.
2. **Reproducible environment:** resolve, pin, and document compatible dependency versions during implementation; check any assessment-specific rubric if supplied later.
3. **Quality baseline:** use a compact fixed set of banking, economics, history, baking-science, and paper questions, plus refusal, interpretation, material ambiguity, and mixed-request cases. Manually inspect evidence support and inference labels during live verification; citation existence alone is insufficient.
4. **Known coverage limit:** Wikipedia extracts/arXiv abstracts cannot answer everything in the accepted scope. Keep the stated Wikipedia-only guidance and transparent partial/insufficient-evidence outcomes; additional providers and full-text research remain outside v1.

## Verification and implementation sequence

After agreeing on the gaps, implement contracts and the LangGraph state/nodes/edges with fake model/tool responses, then independent tool adapters, then the Ollama adapter and provenance-aware renderer, and finally end-to-end checks. No pushes until separately authorized.

Acceptance scenarios:

- A scholarly concept question produces a grounded explanation with references; the restaurant recommendation refuses with zero external tool calls.
- Baking science, regulatory history, and economics examples are accepted; boundary cases follow the agreed scope examples.
- Assert graph paths and state changes: scope refusal, tool loop, early readiness, forced synthesis, citation repair, and terminal error. JSON traces capture ordered node transitions, state summaries/counters, and termination reasons; debug output stays on stderr.
- A valid Wikipedia-only explanation ends without a forced arXiv call. A paper request cannot silently substitute a Wikipedia background answer.
- Every answer item displays retrieved/inference provenance. References include source type, titles, URLs, and paper IDs when applicable; the inference's cited premises appear in actual tool results.
- A paper-discovery question retrieves arXiv metadata/abstracts; returned citations resolve only to actually retrieved sources.
- A scripted model requests one search, sees results, then requests another tool before answering, proving iterative model-directed research.
- Harmless ambiguity records an interpretation and proceeds; material ambiguity requests clarification without searching. Mixed requests answer the supported portion and explicitly identify the unsupported portion. Test keyword-only false refusals.
- Empty searches, provider outages, rate limits, malformed tool calls, repeated calls, missing Ollama/model, and Ctrl-C terminate predictably.
- Tool/HTTP/model budgets and the 60-second invocation deadline hold under fake clocks and mocked clients, including cold-start simulation, hanging requests, retries, repairs, and trace cleanup; synthesis cannot bypass the remaining deadline.
- Parse every trace line as JSON; assert tool arguments/results, timing, short decision summaries, ordered transitions, and exactly one terminal event for handled outcomes. Test secret redaction, unwritable trace directories, and no hidden reasoning fields.
- Unknown citations and unsupported/absent evidence cannot produce fabricated references. Retrieved instructions cannot enable new tools or change controller policy.
- Stdout contains only the user-facing result; diagnostics go to stderr. Run offline pytest first, then separately verify the chosen real Ollama model, Wikipedia, and arXiv APIs. Record actual versions and cold/warm end-to-end latency.

## Technical references

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api): state schemas, nodes, conditional edges, reducers, and recursion limits.
- [Ollama qwen3:1.7b](https://ollama.com/library/qwen3:1.7b): proposed small model with listed tool support; local suitability remains untested.
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling): tool definitions, returned calls, and follow-up tool messages.
- [MediaWiki REST API reference](https://www.mediawiki.org/wiki/API:REST_API/Reference/en): Wikipedia search and page retrieval.
- [arXiv API terms](https://info.arxiv.org/help/api/tou.html): request pacing and API access requirements.
