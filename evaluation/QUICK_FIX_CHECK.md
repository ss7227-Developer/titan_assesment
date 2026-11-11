# Final quick-fix check

Changes: cap oversized positive result counts at 3 before connector dispatch;
record original/effective arguments; strengthen topic-query and direct-answer
prompts; reject reported numbers absent from cited text/metadata. No new tool,
provider, model, graph node, or budget increase. The native-count clamp is verified
by offline regression tests for requests of 5 and 10. Other invalid counts still
fail, and citation safeguards remain active.

Live command:

```bash
python evaluation/run_questions.py --ids 6 --model qwen3.5:4b
```

Run: `.research-agent/evaluations/20261111T224751166485Z/`.
Manual verdict: **partial**, improved from the 4B comparison's failed argument-only
run. Runtime: **7.836 seconds**; 2 tool/HTTP calls; 4 model calls. Both modules
executed once, within the unchanged 60-second invocation limit.

## Usefulness and grounding

The response directly provides one relevant relationship: sustained inversion has
historically preceded U.S. recessions and appears in recession-probability models.
That statement is explicitly present in the actual returned Yield curve extract,
so its citation supports this sampled claim.

It does not supply recent papers. The arXiv query returned empty; the response
discloses this gap rather than inventing publications. Its assessment also produces
awkward/contradictory gap wording about evidence IDs for the explanation. The model
still added years to the arXiv query despite the new prompt. Query reformulation,
assessment consistency and deeper retrieval remain unfinished.

The model requested valid counts of 2 and 3 in this run. Therefore the run does not
isolate the effect of the clamp from prompt/model variability; count normalization
itself is covered by deterministic tests. It demonstrates a useful grounded partial
answer, not a solved paper search or a full-suite pass.

**91 offline tests pass.** CLI tests assert stdout/stderr separation. Numeric
validation is an extra mechanical check: number presence does not prove units,
context, entailment, recency or causality, and inferential claims still need review.
No full eight-question rerun was performed after these final quick fixes.
