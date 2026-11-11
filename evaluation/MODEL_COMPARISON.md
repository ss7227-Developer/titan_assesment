# Installed-model comparison — final wrap-up

Connectors, prompts, graph, citation safeguards and budgets were frozen for this
comparison. No model was downloaded. Tool capability was read from local Ollama's
installed-model inventory, not inferred from parameter count.

## Configuration

Same code fingerprint in all compared runs:
`655e884915dd97d5952fa445dce48fd73a1516ccc33ce51bd3d97f56712d618f`.
Same questions: IDs 3 (credit-risk papers) and 6 (yield curve/recessions).
Same 60-second invocation / 40-second research / 58-second model-work deadlines,
6 tool executions, 10 HTTP attempts, 10 model calls, maximum 3 results/search,
one assessment repair and one answer repair. No citation guard was bypassed.

Installed candidates:

| Model | Tool capable | Approximate model-file size | Role |
| --- | --- | --- | --- |
| qwen3:1.7b | Yes | 1.36 GB | Existing application default / saved baseline |
| qwen3.5:4b | Yes | 3.39 GB | Preferred explicit tester candidate after this comparison |
| llama3.1:8b | Yes | 4.92 GB | Compared alternative; not selected |
| gemma3:270m | No | 0.29 GB | Not eligible for this tool-calling workflow |

Sizes come from local inventory, not measured runtime RAM usage. The model tags are
local installed tags; this is not a general ranking of model families.

## Usefulness and grounding

| Model | Credit-risk paper question | Yield-curve question |
| --- | --- | --- |
| qwen3:1.7b | 4.40s: no usable paper summary; assessment type errors. | 4.50s: no useful explanation/papers; invented evidence ID rejected. |
| qwen3.5:4b | **18.83s: useful partial answer.** Two 2025 papers, IDs/URLs, with summaries supported by returned abstracts. Explicit remaining gaps; assessment repair still fails part consistency. | **2.11s: fails.** Requests 5 Wikipedia results and 10 arXiv results; both exceed the limit of 3 and are correctly rejected. No useful evidence/answer. |
| llama3.1:8b | **16.44s: fails.** Initial result-limit violation, then retrieves papers but supplies an invalid non-ISO publication cutoff. Repair fails, so no substantive summary. | **4.23s: fails.** Initial result-limit violation, then an empty arXiv result. No useful explanation or papers. |

The 4B paper summary correctly describes the data-quality robustness study and
the interpretable-LLM credit-risk review. These claims were checked against the
actual returned abstracts, not merely citation-ID validity. It does not get a full
pass: its stop is an assessment fallback, gaps are awkward/repeated, and it does
not establish exhaustive coverage of recent research.

No candidate solves both cases. The measured improvement is one useful grounded
partial answer, not a new eight-question pass rate. Execution exit code 0 in a
limitation case is not a content pass. Samples are one run per candidate/question;
warm/cold effects and run-to-run variation are not isolated.

## Selected tester configuration

Use the existing `--model` override, without changing application code or defaults:

```bash
python evaluation/run_questions.py --ids 3 6 --model qwen3.5:4b
python agent.py "your research question" --model qwen3.5:4b
```

The default remains `qwen3:1.7b` for reproducibility of earlier runs. The explicit
4B configuration is preferred for the next evaluation because it improved actual
paper-answer usefulness and support. It costs more model-file storage and was
slower on the paper case. The untested six questions must not be assumed improved.

## Artifacts

- 1.7B saved assessment run: `.research-agent/evaluations/20261111T223054322445Z/`.
- 4B run: `.research-agent/evaluations/20261111T223937742561Z/`.
- 8B run: `.research-agent/evaluations/20261111T224018339960Z/`.
- [Captured comparison answers](model-comparison-answers.md).
- [Portable results](model-comparison-results.json), including source metadata/text
  for the useful 4B paper response so its grounding can be inspected.

## Remaining limitations / more time

1. Improve general tool-argument adherence; invalid result counts remain rejected,
   rather than silently increasing limits or weakening validation.
2. Simplify/test evidence-type classification and assessment repair on held-out
   questions. Publication dates are not measurement observation dates.
3. Retrieve bounded relevant Wikipedia sections when introductions omit required
   details; keep this within the existing provider if later authorized.
4. Add claim-support/relevance evaluation beyond ID existence. Preserve explicit
   missing-data limits; no current unemployment observation is available via FRED.
5. Run the preferred model on all eight and a held-out set, with repeated timings,
   before deciding whether to change the application default. Exact token budgeting
   and client/timeout cleanup remain engineering follow-ups.
