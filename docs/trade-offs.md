# Trade-offs

ModelForge uses a language model only where judgement over free text is the task and the output can be checked, and keeps every step that produces a number deterministic. This page states each of those choices, what it cost in the recorded runs, and how far a cheaper hosted model and a local model fall from the default. ModelForge is a reference implementation, not a production system, and the runs below are single runs, not distributions.

Every number on this page is inside one of the two results blocks, rendered by `make results` from [`results/runs.json`](results/README.md); the [results page](results/README.md) defines each field and how a record is written. The prose describes the records and points at the columns.

## Deterministic or a language model

The table is the architecture spec's trade-off table with a fourth column from the records. It adds a row for remediation, which the original table folded into effective challenge; the [design memo](design-memo.md#conceptual-soundness-of-the-pipeline) extends the same table to every node of the run.

| Step | Choice | Why | What it cost in practice |
| --- | --- | --- | --- |
| Brief to plan (`scope`) | Language model, structured output | Judgement over free text; the output is typed and human-edited | The smallest model line: in both hosted records the `scope` column of the cost table is the lowest of the four purposes, and `scope` is a small share of `wall_time_by_node_s` in `runs.json`. Both hosted runs proposed the expected plan (`scope_exact_match`). |
| Profiling, splits, binning, fitting, metrics (`profile`, `split`, `features`, `build`) | Deterministic | Must be reproducible and auditable; no judgement needed | No model cost, and a negligible share of each record's `wall_time_by_node_s`. The same code ran under all three profiles, which is why the parity table differs only in the model-driven columns. |
| Threshold evaluation (`validate`) | Deterministic | Policy, not opinion | Likewise negligible in `wall_time_by_node_s` and free; the thirteen tests do not depend on the profile. |
| Narrative documentation (`document`) | Language model, grounded and checked | Writing is the judgement task; numbers are verified mechanically | One of the two dominant nodes in wall time in every record, and the largest by far in the local run. The checks are where the profiles part: compare the Grounding, Complete and Ungrounded numbers columns. |
| Completeness checks (pre-checks in `challenge`) | Deterministic first | Cheap, exact, frees the challenger for substance | Free. They produced no findings on the default run's document and several on the other two, visible as the pre-check half of the Findings column. |
| Effective challenge (`challenge`) | Language model, isolated | Finding conceptual gaps is judgement; isolation is structural | A minor share of cost and wall time next to `document` and `remediate`; compare the `challenge` column of the cost table. |
| Remediation (`remediate`) | Language model, bounded | Answering a reviewer is judgement; disputes must cite artifacts, the rounds are capped | The most expensive purpose in both hosted records (the `remediate` column) and the longest node in their `wall_time_by_node_s`. The local run failed here on schema errors after its retries. |
| Approvals (`gate_scope`, `gate_model`, `gate_signoff`) | Human | Accountability cannot be delegated | No model cost. What a gate costs is a person's attention, which no field in the records isolates. |

## Parity

One row per recorded run. The local row ends in `needs_attention`, so the metrics only an eval report has are `-`.

<!-- results:parity -->
| Profile | Model | Status | Grounding | Complete | Ungrounded numbers | Findings (pre-check / review; critical / high / medium / low) | Rounds (challenge / remediation) | Sections regenerated | Schema retries | LLM calls | Cost USD | Wall time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hosted-default | anthropic/claude-opus-5-5 | completed, approve_with_conditions | 100.0% | yes (0 flagged) | 0 | 0 / 11; 0 / 4 / 7 / 0 | 3 / 2 | 7 | 0 | 92 | 7.18 | 31:02 |
| hosted-ci-cheap | anthropic/claude-sonnet-5-5 | completed, approve_with_conditions | 81.8% | no (2 flagged) | 1 | 9 / 7; 0 / 12 / 4 / 0 | 3 / 2 | 11 | 0 | 139 | 3.19 | 11:34 |
| local-ollama | ollama/gpt-oss:20b | needs_attention, n/a | - | - | - | 10 / 7; 2 / 14 / 1 / 0 | 1 / 0 | - | 10 | 67 | 0 (local) | 28:59 |
<!-- /results:parity -->

## Cost

Tokens and cost by routing purpose, with the run's cost cap. A local profile has no prices, so its cost is zero by the profile's own accounting.

<!-- results:cost -->
| Profile | Input tokens | Output tokens | scope | document | challenge | remediate | Total | Cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hosted-default | 1169281 | 149474 | 0.04 | 1.91 | 0.73 | 4.50 | 7.18 | 20.00 |
| hosted-ci-cheap | 1355833 | 84859 | 0.02 | 0.84 | 0.23 | 2.10 | 3.19 | 10.00 |
| local-ollama | 398505 | 150130 | 0.00 | 0.00 | 0.00 | 0.00 | 0 (local) | 10.00 |
<!-- /results:cost -->

## Reading the gap

The two hosted runs both completed, sealed and reached a conditional sign-off, and the difference between them is what the cheaper profile bought. The `ci-cheap` profile cost less and finished sooner (the Cost USD and Wall time columns), but it traded grounding for that cost: a lower grounding rate, an incomplete document with flagged sections, a non-zero Ungrounded numbers column, and pre-check findings the default run did not produce. It also made more model calls, read more input tokens and wrote fewer output tokens, and remediation rewrote more of its sections, so part of the saving was spent on repair. In both hosted records `remediate` and `document` together account for most of the cost and most of the per-node wall time, and the deterministic nodes for almost none, so the price of a run is set by how much rewriting the review loop asks for, not by the modeling.

The local `gpt-oss:20b` run cost nothing by the profile's accounting but did not finish. It wrote the document and passed it to the challenger, spending most of its wall time in `document`, then failed in remediation when its answers to the findings missed the response schema after the gateway's retries, and parked in `needs_attention`; the Schema retries column shows how often it missed the schema overall. Its challenger raised critical findings where neither hosted run did, but with no eval report there is no grounding or completeness figure to say whether that reflects a weaker document or a stricter reviewer. The record's note adds a second attempt that was stopped in the `document` node long past the profile's wall-time budget. The local route is therefore supported but not equivalent: the binding constraint is reliable structured output, not cost, and a bank that cannot send data to a hosted model would need a stronger local model before the review loop could close.
