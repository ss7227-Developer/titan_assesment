# Latest full eight-question suite

Model: `qwen3.5:4b`. Run: `.research-agent/evaluations/20261111T225255083690Z`. App fingerprint: `4721f764320cbb76ee76ace6c67b91416348afd9b7c614db6e92a8b641dbb45f`.

All 8 completed in approximately 46.00 seconds summed CLI wall time; each took 1.06–9.89 seconds. No application code changed during this rerun.

**Manual quality: 1 pass, 3 partial, 4 fail.** Original baseline and prior assessment run: 1 pass, 2 partial, 5 fail. A partial is not a full pass.

| # | Topic | Result | Seconds | Finding |
|---|---|---|---|---|
| 1 | Discount window | partial | 8.22 | Supported basic definition, but operating detail is incomplete and gaps say None. |
| 2 | Basel III | fail | 9.89 | Missing capital ratios; explicit limitation rather than a substantive answer. |
| 3 | Credit-risk papers | fail | 3.66 | No papers delivered; arXiv returned empty and the agent stopped. |
| 4 | 2008 vs COVID | fail | 8.17 | No supported two-period comparison; explicitly discloses missing/irrelevant evidence. |
| 5 | Current unemployment | partial | 4.21 | Safe current-data limitation; no invented rate/trend, but no substantive contextual explanation. |
| 6 | Yield curve/papers | partial | 5.88 | Relationship claim matches the returned Yield curve extract; no papers and inconsistent gap wording. |
| 7 | Restaurant | pass | 1.06 | Correct refusal with no external calls. |
| 8 | Quantum encryption | fail | 4.92 | Declines unsupported claims after irrelevant retrieval; no substantive explanation. |

The gain is modest and largely safer handling of missing evidence. The yield-curve explanation is restored versus the prior assessment run; it was already partially present in the original baseline. Current unemployment no longer fabricates a number, but is a safe limitation rather than a useful data answer. Four questions still fail substantively.

Paper search regressed relative to the earlier 4B spot check that produced two useful papers. This shows query/retrieval variability: a single successful spot check did not establish reliable performance. This is a joint model/configuration/code comparison, not an isolated model-capacity experiment.

[Captured answers](latest-suite-answers.md). Previous comparisons and raw trace files are preserved.
