This page is the reference for the challenger review; the README is the front door.

# Challenger review

The `challenge` node is an independent reviewer inside the run: it did not build the model and did not write the document, and it runs after `document` and before the sign-off gate.

**What it sees.** Its model context is assembled in one place (`challenger.py`) from lineage, by artifact id: the rendered document, the validation report through the same excerpt projection the documentation agent gets, the modeling plan read from the run's recorded plan artifact (`runs.plan_ref`, not the in-memory run state), and the knowledge passages its own queries retrieve. The pre-checks below also read the completeness report and, by id, the artifacts the document's markers cite; neither is sent to the model. The challenger never reads the brief, the scope notes, the section drafts and their grounding reports, a raw model response or the column dictionary. `test_challenger_isolation.py` proves it: the test plants a distinct sentinel string in each of those developer-side artifacts (the brief's constraints, the scope rationale and open questions, a superseded section draft and its grounding report, a stored raw response, a column description), runs `challenge` through a recording gateway and a recording store, and asserts that no sentinel appears in any message of any challenge call and that every artifact the node read is of an allowed kind or cited by the document. Its calls use their own purpose, `challenge`, so a profile can route the reviewer to a different model from the developer agents; the shipped profiles route it to the same model. Redaction applies to its calls as to every other.

**Deterministic pre-checks.** Every challenge round first runs seven checks over the rendered document, before any model call. Each failing check becomes a finding with a stable key built from ids and ordinals, so a later round can close it when the check stops firing:

| Key | Severity | Fires when |
| --- | --- | --- |
| `missing_section:<id>` | `critical` | a required template section is absent or holds only the author-attention banner, or the completeness report lists it as missing |
| `bannered_section:<id>` | `high` | a section starts with the `REQUIRES AUTHOR ATTENTION` banner above further content |
| `undiscussed_test:<test_id>` | `high` for `fail`, `medium` for `warn` | a failing or warning validation test is neither cited by a marker nor named anywhere in the document (text inside the banner block or a code span does not count) |
| `unaddressed_na:<test_id>` | `high` | a not-applicable test (such as an out-of-time or sensitivity test on data without vintages) is not named in a sentence that says it is not applicable, not available, n/a, not assessed, could not be assessed or unavailable |
| `unreported_test:<test_id>` | `medium` | a passing out-of-time (`disc.*.oot`) or sensitivity (`sens.*`) test is neither cited by a marker nor named anywhere in the document |
| `reverify:<id>` | `high` | the grounding check, re-run over the section against the sealed artifacts and the knowledge index, finds an ungrounded number, an unresolved marker, an uncited regulatory sentence or an unknown passage; a bannered section is left to `bannered_section` |
| `miscited_regulation:<id>:<n>` | `medium` | sentence `n` of the section names a regulation and cites a known passage that neither belongs to that regulation's document nor names it (an unknown passage is left to `reverify`) |

The `reverify` check is the documentation agent's grounding check run again from the reviewer's side, so a number changed after the section was written is caught without a model.

**The review.** In round 1 the challenger then makes two calls. A query-planning call sees the template's section titles, the plan's regulatory scope, the status of each validation test and the pre-check findings, and returns up to five queries; each query fetches the top three passages from the knowledge corpus. The review call sees the rendered document, the validation excerpt, the plan, the retrieved passages and the pre-check findings (so it does not repeat them), and returns findings, each with a severity, a category (`conceptual_soundness`, `data`, `methodology`, `performance`, `documentation`, `governance`, `limitations` or `monitoring`), a title, a description, evidence and one required action.

**Evidence grammar.** Each evidence item is exactly one of: a template section id (`performance`); an artifact marker `[A:validation#<path>]` or `[A:plan#<path>]`, whose path follows the citation path grammar of the knowledge layer and must resolve in that excerpt (no other alias is allowed); or a passage marker `[K:<citation_id>]` naming a passage retrieved for this review. A finding with no evidence, or with an item outside the grammar, is rejected by a validation hook and the gateway retries with the problems listed, as for a schema failure, then parks the run. Stored evidence has its aliases expanded to artifact ids.

**Severities.** The system prompt binds the model to these meanings, and the pre-checks use the same scale:

- `critical`: a required section or validation result is missing and the document cannot be relied on.
- `high`: a material weakness in data, method, performance, or an ungrounded statement, that must be fixed before use.
- `medium`: a weakness to fix or to accept as a sign-off condition.
- `low`: editorial.

**Findings.** Findings get ids `F-1`, `F-2`, ... in the order they are raised, pre-check findings before review findings, numbered on across rounds. Each records its source (`precheck` or `review`), the round that raised it, its status (`open`, `accepted`, `disputed` or `closed`), the developer's response and evidence, the challenger's verdict and the round that closed it. The `findings` artifact is cumulative: every round stores the whole list with current statuses, so the sealed manifest's `findings` ref is the full history. Its summary carries `n_findings`, `max_severity` (over every finding, closed ones included), `max_open_severity` (over the findings still open or disputed, null when none), `by_severity`, `by_status`, `by_source` and `round`, and the sign-off gate's summary shows it with the remediation responses. Each finding is also a row of the `findings` table, keyed `<run_id>/F-<n>`. `status` prints a line after the cost line once a run has findings, for example

```
findings: 6 (open 0, accepted 0, disputed 1, closed 5; max severity high)
```

and `status --json` includes the rows.

[Back to the README](../../README.md)
