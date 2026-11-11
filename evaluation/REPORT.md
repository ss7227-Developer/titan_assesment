# Eight-question live evaluation

Run: 2026-11-11, starting 21:56 UTC. Local `qwen3:1.7b`, current working-tree
application, real Wikipedia/arXiv APIs, separate serial CLI processes. This is one
sample per question, not a reliability or cold-start benchmark. No application
logic, tools, or model were changed for this evaluation.

**Content results: 1 pass, 2 partial, 5 fail.** All eight processes exited normally
and completed under six seconds. This demonstrates execution speed and API
connectivity; it does not demonstrate research quality. All displayed reference
IDs exist in retrieved results, yet many claims are unsupported by those results.

## Results

| # | Topic | Content result | Time | Actual tools | Main finding |
| --- | --- | --- | --- | --- | --- |
| 1 | Discount window | Partial | 4.28s | Wikipedia | Core definition is supported; repeats it and omits important operational detail. |
| 2 | Basel III capital requirements | Fail | 3.62s | Wikipedia | Gives background, not the requested ratios/buffers. Stops despite missing evidence. |
| 3 | Recent ML credit-risk research | Fail | 3.66s | Wikipedia | Does not call arXiv or identify papers; generic claims are presented as recent research. |
| 4 | Fed response: 2008 vs COVID | Fail | 4.28s | Wikipedia | No COVID-specific sources; conflates fiscal programs and Fed monetary policy. |
| 5 | Current unemployment / past year | Fail | 3.00s | Wikipedia | Invents a current observation and trend unsupported by returned text. |
| 6 | Yield inversion / recession papers | Partial | 5.48s | Wikipedia + arXiv | Both tools used; explanation is broadly supported, but a 2018 paper is called recent. |
| 7 | Best NYC restaurant | Pass | 0.70s | None | Correct refusal, zero external searches. |
| 8 | Quantum computing / banking encryption | Fail | 3.67s | Wikipedia | Plausible claims, but the actual extracts do not support quantum-specific assertions or banking deductions. |

Full captured answers: [answers.md](answers.md). Exact original questions and
expected behaviors: [questions.json](questions.json). Partial is not a full pass:
it means a useful supported component was present while material requirements
were missing.

## Unemployment policy

Keep the question in factual research scope and allow a **caveated contextual
answer**, rather than treating it like the restaurant recommendation. Without
current data access, the expected response explicitly says that it cannot verify
the current rate or year-over-year change. It may explain the measure or discuss
dated historical evidence actually retrieved. Any deduction must be labeled as
inference and supported by cited premises. A guessed current number or trend is
not an acceptable substitute for a time-stamped observation.

The captured unemployment output fails this policy: its source extracts contain
definitions/context, not the reported rate or comparable year-ago data. The number
in the raw answer is **not verified or endorsed**. No FRED tool was implemented.

## What the traces show

- The six non-refusal questions other than yield-curve synthesis used one actual
  Wikipedia search each. None was forced to stop by the two-no-new-sources rule.
  The model declared readiness despite major evidence gaps.
- The yield-curve case executed three searches (two Wikipedia, one arXiv), showing
  that the old two-successful-search cap is gone.
- All eight runs retained the original question and final stdout answer in their
  traces. The restaurant case made one scope model call and no provider calls.
- Citation-ID validation passed, including cases with unsupported claims. A valid
  reference ID establishes existence, not entailment or adequate question coverage.
- These are model outputs, not hard-coded answers or mocked provider responses.

## Next improvements indicated by this set

1. Make evidence sufficiency explicit: check which parts of the question have been
   established before the agent stops. Missing numbers/comparison sides must
   trigger another search or an honest limitation.
2. Strengthen paper-request behavior: retrieve academic papers for academic
   requests and check actual publication dates against the requested recency.
3. Retrieve relevant portions of Wikipedia articles rather than relying only on
   generic introductory extracts when the requested detail is elsewhere. This can
   remain inside the existing Wikipedia tool; no additional provider is necessary.
4. Enforce source support and provenance more reliably. Do not allow model-memory
   facts to appear under `Retrieved evidence` merely because the cited article is
   broadly related. Grounding evaluation is the highest-priority quality gap.
5. Explicitly handle unavailable current measurements with a contextual response;
   inference labels alone do not make an unsupported numerical observation valid.

The selected small model is fast but has not met this content evaluation. This
single run does not isolate model capacity from prompting, retrieval depth, or
stopping-policy issues. Those should be addressed and compared before attributing
all failures to model size.

## Audit references

These primary references were consulted only to review the outputs, not supplied
to the agent and not added as application tools:

- [Basel Framework risk-based capital requirements](https://www.bis.org/committees/bcbs/basel-framework/standard/rbc?allChapters=true)
  contains capital ratios/buffers omitted from the Basel answer.
- [Federal Reserve comparison of crisis tools](https://www.federalreserve.gov/econres/notes/feds-notes/implementing-monetary-policy-in-an-ample-reserves-regime-when-in-crisis-note-3-of-3-20201002.html)
  supports separating monetary-policy tools and evaluating both crisis episodes.
- Unsupported-claim judgments were checked against the **actual saved tool
  extracts/abstracts**, not merely the full articles linked by the model.

## Reproduce and inspect

```bash
source .venv/bin/activate
python evaluation/run_questions.py
# Optional subset:
python evaluation/run_questions.py --ids 3 5 6
```

The runner saves stdout, stderr, copied JSONL traces, timing/counters, original
questions and manual-review placeholders under `.research-agent/evaluations/`.
The reviewed run is `.research-agent/evaluations/20261111T215604871330Z/`; its
`results.json` records manual verdicts and an application-source SHA-256 fingerprint.
Generated traces stay local and ignored by Git; the portable review and captured
answers are retained in this evaluation directory. Re-running does not overwrite
the previous run. Normal offline tests remain separate: `python -m pytest -q`.
