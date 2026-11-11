# Completion-assessment comparison

The graph still has the same scope, research, tools, synthesis, validation and
render nodes. The model is still local `qwen3:1.7b`; sources are still Wikipedia
and arXiv. No answers, thresholds, or branches were keyed to the eight questions.

## Saved runs

- Baseline: `.research-agent/evaluations/20261111T215604871330Z/`.
  [Original manual review](REPORT.md) and [original captured answers](answers.md)
  are preserved unchanged.
- Final assessment-enabled run: `.research-agent/evaluations/20261111T223054322445Z/`.
  [New captured answers](assessment-answers.md) and
  [portable assessment/decision events](assessment-decisions.json).
- The final run's application SHA-256 fingerprint is
  `655e884915dd97d5952fa445dce48fd73a1516ccc33ce51bd3d97f56712d618f`.
  It matches the tested application files. Intermediate smoke/protocol-test runs
  are saved separately; the table below uses only the final full run.

## What changed

After the initial model-selected tool call, the research node requests structured
question parts, supporting evidence IDs, missing evidence, a short operational
reason, and an action: search, finish, or limited. Focused follow-ups name the same
existing tools. Plain non-tool output is no longer accepted as implicit readiness.

The application rejects invalid source IDs, background articles passed off as
paper evidence, observation periods absent from measurement source text, paper
dates outside a stated recency window, and complete finishes that still declare
gaps. Comparison completion requires separately assessed subjects/periods. These
rules operate on general evidence types; correct type classification still depends
on the model. They do not constitute semantic entailment checks.

There is one assessment repair per run, counted inside the existing budgets. On
failure, the application can recover mechanically valid source mappings and stop
with explicit unverified-completeness gaps. It never promotes unknown IDs or
unassessed sources automatically. Synthesis sees the assessment and only its
supported sources. The renderer appends gaps and the stopping limitation.

## Answer-quality comparison

Manual content scores remain **1 pass, 2 partial, 5 fail**. This aggregate hides
an improvement in the unemployment case and a regression in the yield-curve case.
The main improvement is safer, inspectable limitations—not complete answers across
the set. Safe abstention is not counted as a full substantive answer.

| # | Topic | Baseline → new | Seconds: old → new | Result |
| --- | --- | --- | --- | --- |
| 1 | Discount window | Partial → Partial | 4.28 → 8.36 | Gives a supported definition and identifies missing operating mechanics; assessment inconsistencies still prevent completeness. |
| 2 | Basel III requirements | Fail → Fail | 3.62 → 5.11 | Correctly identifies missing details instead of pretending generic background answers the question. Ratios remain unanswered. |
| 3 | Recent ML credit-risk research | Fail → Fail | 3.66 → 4.40 | Now retrieves arXiv, but confuses paper publication dates with measurement dates and cannot deliver a useful summary. |
| 4 | 2008 versus COVID policy | Fail → Fail | 4.28 → 5.41 | Avoids the invented comparison; still fails to retrieve/establish both episodes and repeats broad searches. |
| 5 | Current unemployment/change | Fail → Partial | 3.00 → 8.32 | Removes the unsupported current rate/trend and states both gaps. Gives little useful context; labels absence-of-evidence as retrieved content. |
| 6 | Inversions/recessions/papers | Partial → Fail | 5.48 → 4.50 | Regression: assessment invents a source ID, which is rejected, but the supported explanation is then lost. No useful paper synthesis. |
| 7 | Restaurant recommendation | Pass → Pass | 0.70 → 0.70 | Correct refusal, no provider calls. |
| 8 | Quantum/banking encryption | Fail → Fail | 3.67 → 4.24 | Avoids unsupported quantum-specific assertions, but misclassified assessment parts prevent a substantive answer. |

Average wall time rose from **3.585s to 5.130s**. All eight final invocations exited
normally and remained below the existing 60-second deadline; maximum was 8.361s.
These are single-run samples, not statistically established latency/reliability
measurements. No comparison claim is based on exit code alone.

## Examples visible in the new traces

- The unemployment assessment states that neither the current observation nor
  year-ago evidence is established. The final output does not supply a made-up
  rate or decline. The model still incorrectly types these parts as ordinary facts,
  which is a remaining weakness rather than proof of reliable measurement handling.
- The credit-risk case starts with arXiv rather than generic Wikipedia background.
  However, a later assessment labels paper dates as dated measurements. The date
  guard rejects that mapping and logs the specific errors.
- The yield-curve assessment proposes evidence ID 4 even though it was not retrieved.
  Both the initial assessment and its one repair are rejected; ID 4 never becomes
  a valid citation in the output. The result is safe but not useful enough.
- The Basel III model proposes the same search repeatedly. Cache hits avoid
  repeated provider requests, and the two-no-new-sources limit stops the loop.
  The missing detail is now explicit; focused reformulation is still unreliable.

## Regression verification

**84 offline pytest tests pass.** Relevant cases cover:

- native initial search followed by explicit completion;
- missing evidence leading to a focused follow-up;
- limited completion passed into synthesis with visible gaps;
- no full finish with stated gaps, and no plaintext readiness shortcut;
- paper-provider requirements, publication cutoffs, comparison parts, and
  observation periods distinguished from retrieval dates;
- one assessment repair, safe partial recovery, and unknown-ID removal;
- synthesis input limited to assessed sources and citations tied to assessed parts;
- unchanged time/call limits, cache behavior, and question/answer JSON tracing.

All final-run traces were also checked for terminal assessments, matching captured
stdout, input-size caps, tool/HTTP/model budgets, and the total deadline. Every
non-refusal case has a logged final finish/limited assessment, including clearly
labeled application-fallback origin when model repair failed.

## Remaining work

The model still struggles to classify evidence types, keep question parts stable,
reformulate weak queries, and produce consistent assessments after repair. The
first assessment implementation should therefore be treated as an inspectable
safety/control improvement, not a solved research-quality problem.

Next evaluation should separate these protocol/type errors from retrieval-depth
and claim-support errors, using held-out questions as well as this set. Wikipedia
introductions still omit details needed for some questions. Citation mappings and
valid dates still do not prove that a source entails a generated claim. A model
comparison could be run explicitly later; no larger model was silently substituted.
