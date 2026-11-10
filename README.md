# ModelForge

ModelForge is a governed, multi-agent copilot for the credit risk model development lifecycle. Given a raw credit dataset and a modeling brief, it produces a validated probability-of-default (PD) model, an SR 11-7 style model development document, an independent "effective challenge" review, a tamper-evident audit trail, and a model inventory entry, with human approval gates at the points where a bank would require them.

The core toolkit (`packages/core`, `modelforge_core`) is the deterministic, LLM-free layer underneath. It loads a dataset into a canonical schema, profiles it and screens for leakage, builds train / validation / out-of-time splits, fits monotone WoE binning and IV / correlation feature selection, trains a WoE logistic scorecard champion and a LightGBM challenger, calibrates the champion, and validates it: discrimination (AUC, KS, Gini with bootstrap intervals), calibration (per-grade binomial, Hosmer-Lemeshow, expected / observed), stability (PSI, CSI), sensitivity (coefficient signs, WoE monotonicity, bin shifts), and a champion-challenger benchmark. The champion is refit iteratively, dropping any feature whose coefficient has the wrong sign, and these drops are recorded in the feature selection artifact. Every result is checked against documented thresholds into a single validation report. Models are stored as JSON (scorecard) and LightGBM native text (challenger); nothing is pickled.

The orchestrator (`packages/orchestrator`, `modelforge_orchestrator`) turns that toolkit into a governed run. A YAML brief starts a LangGraph run whose nodes call the toolkit only through MCP servers (`packages/mcp`). Every input and output is a content-addressed artifact, and every step, tool call and decision is an event in a hash-chained lineage store in Postgres (`packages/lineage`). The run stops at three human gates (scope, model, sign-off), with four-eyes checks on the last two, and a sealed run can be replayed to reproduce every deterministic artifact hash. This is a reference implementation, not a production system: sub-project 3 added the LangChain model gateway, through which the scope and documentation agents run, and sub-project 4 added an independent challenger agent, a bounded remediation loop between the challenger and the documentation agent, and an eval harness.

## Quick start

```bash
uv sync --all-groups
make lint test   # ruff, pyright, import-linter, then the full suite (slow tests use real data)
make example     # end-to-end run on the Polish data, out-of-time vintage 5
```

`make example` downloads the dataset into `data/` on first use, prints one line per validation test plus the overall status, and writes `scorecard.json`, `gbm_model.txt`, `validation_report.json`, `binning_tables.json`, `column_dictionary.json` and `profile.json` under `out/polish_bankruptcy/`. For the second dataset:

```bash
uv run python examples/core_pipeline.py --dataset taiwan_bankruptcy --oot-vintages ""
```

Other flags: `--data-dir` (default `data`), `--out` (default `out`), `--seed` (default 42), `--max-features` (default 20).

The slow tests read the cached datasets from `data/` at the repository root; set `MODELFORGE_DATA_DIR` to point them at another directory (for example a shared cache).

## Run a governed model build

```bash
make db-up                    # Postgres via Docker Compose
uv run modelforge migrate     # lineage tables and LangGraph checkpoint tables
uv run modelforge run examples/briefs/synthetic.yaml --requester alice
```

`run` creates the run, executes it in the foreground to the first gate and prints `<run_id> <status>` (here `awaiting_scope`). Each `approve` records the decision and continues to the next gate or the end:

```bash
uv run modelforge approve <run_id> scope --decision approve --approver alice
uv run modelforge approve <run_id> model --decision accept --approver bob
uv run modelforge approve <run_id> signoff --decision approve --approver bob
uv run modelforge status <run_id> --json   # run, steps and artifact refs with summaries
uv run modelforge replay <run_id> --json   # rerun and compare deterministic artifact hashes
uv run modelforge replay <run_id> --rerun-llm --json   # also call the agents again
uv run modelforge verify <run_id>          # walk the event hash chain, artifacts and manifest
```

`examples/briefs/polish-pd.yaml` runs the same sequence on the Polish data. Gate decisions:

| Gate | Decisions |
| --- | --- |
| `scope` | `approve`, `edit`, `reject` |
| `model` | `accept`, `adjust`, `reject` |
| `signoff` | `approve`, `approve_with_conditions`, `reject` |

`edit` and `adjust` take a modeling plan with `--plan`; `approve_with_conditions` takes one or more `--condition`. The `model` and `signoff` decisions must come from someone other than the requester, and `approve` at `signoff` is refused while a critical or high finding of the challenger is open or disputed (see "Remediation loop"). Run statuses are `queued`, `running`, `awaiting_scope`, `awaiting_model`, `awaiting_signoff`, `needs_attention`, `completed`, `rejected` and `aborted`. A run that fails a tool call parks in `needs_attention`; `uv run modelforge resume <run_id>` retries it from its last checkpoint and `uv run modelforge abort <run_id>` ends it. A run parked by its cost cap resumes with a higher cap: `uv run modelforge resume <run_id> --cost-cap 20` sets the cap in USD before re-enqueueing (allowed only from `needs_attention`). `run`, `approve`, `resume` and `replay` accept `--detach` to only enqueue and leave execution to a worker (`uv run modelforge worker`).

The run manifest records every artifact hash, and `replay` reproduces each deterministic artifact byte for byte. A plain replay does not call a model: it copies the original's modeling plan, scope notes and final document sections and re-renders the document, so the document refs match too, and it copies the original's findings and responses instead of running the challenger and the remediation loop. `--rerun-llm` runs the scope, documentation, challenger and remediation agents again; the rendered document, completeness, findings and responses refs are then listed under `regenerated` instead of being compared, and `ok` ignores them. `verify` exits non-zero if an event or artifact has been altered.

## MCP servers

The toolkit is exposed through five MCP servers over stdio: `data-profiler`, `credit-modeling`, `validation`, `doc-renderer` and `knowledge`. Each runs with:

```bash
uv run modelforge-mcp <server>
```

They read `MODELFORGE_ARTIFACT_DIR` (the artifact store) and `MODELFORGE_DATA_DIR` (the dataset cache); `doc-renderer` also reads `MODELFORGE_TEMPLATES_DIR`, and `knowledge` reads its corpus from `MODELFORGE_KNOWLEDGE_DIR`. To use a server from a desktop MCP client, add it to the client's configuration, for example:

```json
{
  "mcpServers": {
    "modelforge-credit-modeling": {
      "command": "uv",
      "args": ["run", "--project", "<repo>", "modelforge-mcp", "credit-modeling"],
      "env": {
        "MODELFORGE_ARTIFACT_DIR": "<repo>/artifacts",
        "MODELFORGE_DATA_DIR": "<repo>/data"
      }
    }
  }
}
```

The orchestrator starts the servers itself as `python -m modelforge_mcp <server>`. A custom `MODELFORGE_MCP_COMMAND` must accept a trailing `<server> --marker <uuid>` argument pair: the worker tracks the server processes it started by that marker. `make goldens` regenerates the golden artifacts.

## LLM gateway

Every model call goes through one gateway (`packages/gateway`, `modelforge_gateway`) built on LangChain chat models: `langchain-anthropic` for Anthropic, `langchain-ollama` for Ollama and `langchain-openai` pointed at LM Studio's OpenAI-compatible local server. No node imports a provider SDK. Each call names a purpose (`scope`, `document`, `challenge`, `remediate`, and later `judge`), and the run's profile routes that purpose to a provider, model and effort. The output is structured: the gateway validates it against a Pydantic schema and retries a failed validation at most twice.

Profiles live in `profiles/<name>.yaml` (`MODELFORGE_PROFILES_DIR`):

| Profile | Provider | Model | Effort (scope / document / challenge / remediate / judge) | Prices (USD per million tokens: input, output, cache read, cache write) |
| --- | --- | --- | --- | --- |
| `default` | `anthropic` | `claude-opus-5-5` | low / high / high / medium / medium | `claude-opus-5-5` 4.0, 20.0, 0.20, 5.0; `claude-sonnet-5-5` 2.0, 10.0, 0.20, 2.5 |
| `ci-cheap` | `anthropic` | `claude-sonnet-5-5` | low / medium / medium / low / low | same table as `default` |
| `local-ollama` | `ollama` | `gpt-oss:20b` | not set | none (local, costs 0) |
| `local-lmstudio` | `lmstudio` | `google/gemma-4-26b-a4b-qat` | not set | none (local, costs 0) |
| `fake` | `fake` | `scripted-v1` | low for every purpose | none (offline, costs 0) |

`MODELFORGE_PROFILE` selects the profile when a run is created; it is recorded on the run, and the worker uses the recorded profile for every later step, so a run never switches provider. `run` exits with code 2, before creating the run, on an unknown profile or one whose credential is missing. The Anthropic key is read from `ANTHROPIC_API_KEY`, normally kept in the git-ignored `.env` at the repository root:

```bash
ANTHROPIC_API_KEY=...
MODELFORGE_PROFILE=default   # optional
```

`MODELFORGE_ANTHROPIC_BASE_URL` redirects Anthropic traffic; the inherited `ANTHROPIC_BASE_URL` is never read. The local providers take `MODELFORGE_OLLAMA_BASE_URL` (default `http://localhost:11434`), `MODELFORGE_LMSTUDIO_BASE_URL` (default `http://localhost:1234/v1`), and `MODELFORGE_OLLAMA_MODEL` and `MODELFORGE_LMSTUDIO_MODEL` to override the profile's model. `MODELFORGE_OLLAMA_NUM_CTX` (default `32768`) sets Ollama's context window, whose server default is too small for the documentation prompts.

**Cost accounting.** Every attempt writes an `llm_calls` row with the purpose, provider, model, input, output and cache tokens, cost in USD, latency, the prompt's name, version and hash, and a reference to an `llm_response` artifact holding the raw response with its request hash. The run's running total is kept in Postgres and checked before each attempt, schema retries included: when what has been spent plus the projected cost of the next call would exceed the run's cap (10 USD unless `MODELFORGE_COST_CAP_USD` sets another value for new runs), nothing is sent and the run parks in `needs_attention`. `resume --cost-cap <usd>` raises the cap and continues. `status` prints a `cost: <spent> USD / cap <cap> USD` line, and `status --json` includes the `llm_calls` rows.

**Redaction.** Agents never see dataset rows or artifacts, only excerpts: per-kind projections that keep aggregates and drop row ids, bins, model text and the dataset's original column names. Before any call, the gateway scans every outbound message, schema-retry messages included, for e-mail addresses, IBAN-like tokens and runs of nine or more digits (not the digits of a decimal fraction or a `k_` citation id), and for restricted terms: the brief's optional `restricted_terms` on every call, and the dataset's original column names once the dataset is loaded, so on every documentation call. The scope prompt shows the brief without its `restricted_terms` list, so a brief that lists a term can still pass `scope`; the term anywhere else in the brief refuses the call. A hit refuses the call (it never masks), records the pattern name and message role but not the matched text, and parks the run.

**Prompts.** Prompts are versioned files under `prompts/<agent>/<name>.v<N>.md` (`MODELFORGE_PROMPTS_DIR`) with YAML front-matter and a Jinja2 body. The hash of each file is recorded in a `prompts` row and on every `llm_calls` row, so a document section traces to the exact prompt text. Editing a prompt without bumping its version is a configuration error.

**The `fake` profile.** `fake` is a scripted stand-in, not a recording of a real model. It reads the fenced blocks the prompts mark (the brief, the defaults, the artifact excerpts and the retrieved passages) and builds its answers deterministically from them: the modeling plan the brief implies, a fixed set of queries per section, and sections written from a script that copies numbers out of the excerpts with the same rounding rule the grounding check applies. The challenger and remediation calls are scripted the same way (see "Remediation loop"). It needs no network and costs nothing. It is the default profile for the test suite and for the Docker image, so everything runs offline; it shows that the pipeline, lineage and checks work, not how well any model writes.

## Knowledge layer

The agents cite a small corpus in `knowledge-base/` (`MODELFORGE_KNOWLEDGE_DIR`), served by the `knowledge` MCP server (`packages/knowledge`, `modelforge_knowledge`):

| id | Kind | What it is | Source |
| --- | --- | --- | --- |
| `sr11-7` | primary | SR 11-7, Supervisory Guidance on Model Risk Management (2011), full text; superseded, kept as the structure the development document follows | Federal Reserve Board and OCC, public domain |
| `sr26-2` | primary | SR 26-2, Revised Guidance on Model Risk Management (2026), letter and attachment, full text | Federal Reserve Board, FDIC and OCC, public domain |
| `occ-2026-13` | summary | The OCC bulletin transmitting the same revised guidance | Office of the Comptroller of the Currency; summary in our own words |
| `cecl-asc-326` | summary | CECL expected credit loss concepts relevant to PD | FASB ASU 2016-13 (Topic 326); summary in our own words |
| `ifrs9-ecl` | summary | IFRS 9 staging and lifetime expected credit loss | IASB, IFRS 9; summary in our own words |
| `basel-irb` | summary | PD, LGD and EAD under the internal ratings-based approach | Basel Committee, CRE30 to CRE36; summary in our own words |
| `modeling-standards` | internal | Required document sections, default thresholds, mandatory validation tests, the two dataset proxies | This project |
| `modelforge-governance` | internal | How this system governs itself: gates, four-eyes rule, lineage, replay, redaction | This project |

The corpus is chunked by heading and indexed with BM25 (no embeddings). Each chunk has a stable citation id, `k_` followed by twelve hex characters of a hash of its document, section and text. The `knowledge` server has three tools: `search_regulation` (ranked passages for a query, optionally restricted to some documents), `get_passage` (one passage by citation id) and `get_index` (the stored index). The index is a content-addressed `knowledge_index` artifact pinned in every run manifest, so a replay compares it like any deterministic artifact and detects a changed corpus.

**Citations.** Generated text cites with two markers: `[A:<artifact_id>#<path>]` for a value in a run artifact and `[K:<citation_id>]` for a knowledge passage (the agent writes an alias such as `[A:discrimination#by_split.oot.auc]`, which the node expands to the artifact id). A path is a sequence of dotted keys. A step applied to a list selects the element whose discriminator equals the step, trying `split`, `test_id`, `feature`, `grade` and `name`; a discriminator that itself contains dots is written in brackets, as in `tests[disc.auc.oot].status`; and `.count` gives a list's length. The same grammar resolves on the excerpt the agent saw and on the stored artifact.

**Grounding checks.** Every drafted section is checked before it is stored. Every number in a sentence must match a value cited by an `[A:]` marker in that sentence, at the number's own precision (a value written to three significant figures is compared after rounding to three); every marker must resolve; and a sentence that names a regulation must carry a `[K:]` marker naming a passage retrieved for that section. A section that fails is regenerated once with the failures fed back to the model; if it still fails, it is stored with a visible `REQUIRES AUTHOR ATTENTION` banner listing the failures, and the `document_sections` summary counts it in `n_flagged_sections`. Nothing is silently dropped.

**Regulatory framing.** SR 26-2 superseded SR 11-7 and SR 21-8 on 17 April 2026, and it places generative and agentic AI outside its scope, while broader risk management expectations still apply to such tools. This project keeps SR 11-7's document structure for the development document, because that is the structure model validators know, and governs its own agents anyway: gates, four-eyes approval, lineage, cost caps, redaction and grounding apply to every model call even though the guidance does not require them.

## Challenger review

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

## Remediation loop

When a challenge round leaves a finding `open` and the bound allows another round, the run goes to `remediate`, where the developer side answers.

**Responses.** `remediate` makes one call per open finding (purpose `remediate`). The call sees the finding, the current bodies of the sections its evidence names (every section when it names none), and the plan and artifact excerpts those sections may cite. It accepts the finding, with a concrete action and the template sections to regenerate, or disputes it; a dispute must carry at least one `[A:<alias>#<path>]` marker that resolves in those excerpts. A validation hook enforces both rules and rejects unknown section ids. The finding becomes `accepted` or `disputed`, with the response and its evidence recorded on the finding and its row.

**Regeneration.** The union of the sections the accepted findings name is rewritten through the documentation agent's own write path (query planning, retrieval, writing, the grounding check, one regeneration and the banner on a second failure), under the remediation prompts and with the findings that name each section attached in a block of their own. The node stores a new `document_sections` (the untouched sections copied), re-renders the document, checks its completeness again, and stores a cumulative `responses` artifact (summary: `n_responses`, `n_accepted`, `n_disputed`, `n_sections_regenerated`, `round`) and the findings with their new statuses. A round that regenerates nothing re-renders nothing. The run then returns to `challenge`.

**Re-review.** Every later challenge round re-runs the pre-checks over the re-rendered document and settles their findings without a model: a pre-check finding whose check no longer fires closes (verdict "check passes after remediation"), one whose check still fires is `open` again (a disputed one stays `disputed`), and a check that fires with no live finding of its key raises a new pre-check finding. One re-review call then receives the review findings the developer accepted or disputed, with their responses, the sections regenerated in the last remediation round with their new bodies, and the validation and plan excerpts, and must return exactly one verdict per finding (`closed`, `open` or `disputed`, with a note); no call is made when there are none. The re-review raises no new model findings.

**The bound.** The brief's optional `remediation_rounds` (default 2, from 0 to 3; 0 means review only) caps the remediation rounds. `challenge` routes to `remediate` while some finding is open and fewer rounds than the bound have run, otherwise to the sign-off gate, so the longest path at the default is `document -> challenge -> remediate -> challenge -> remediate -> challenge -> gate_signoff`. A round that leaves nothing open exits early, and whatever is still open when the bound is reached goes to the gate as it is.

**Sign-off rule.** `approve` at the `signoff` gate is refused while any finding of the run with severity `critical` or `high` is `open` or `disputed`. The CLI prints the refusal, naming each blocking finding's id, severity and title, and exits 2; `approve_with_conditions` and `reject` stay available, so a reviewer can sign off with the findings as conditions or send the model back. Medium and low findings, and disputed ones below high, never block.

**Replay.** A plain `replay` makes no model call: the challenger and remediation nodes copy the original's findings and responses with their final statuses, the document node copies the final, remediated sections, and the deterministic refs, the document included, are compared as before. `replay --rerun-llm` runs the challenger and the remediation loop again with the other agents, and lists the rendered document, completeness, findings and responses refs under `regenerated`.

**Under `fake`.** The `fake` profile's findings and responses are scripted. Its review always raises two findings: a medium monitoring finding ("Monitoring thresholds are not quantified", citing the monitoring section and the plan's PSI warning threshold) and a low governance finding ("Approver identities are not named in the governance section"). It adds a medium data finding when the plan's target names a proxy that the limitations section does not mention, and a high performance finding for each performance sentence that claims a test "was performed" without a marker. The scripted developer always disputes the governance finding (approvals are recorded in lineage, not in the document body) and any proxy finding, citing the plan, and accepts every other finding, regenerating the sections its evidence names; the scripted re-review closes an accepted finding whose section was regenerated and keeps disputes disputed. This shows that the loop, its bound, its lineage and the sign-off rule work, not how well any model reviews a document.

## Evals

Evaluation runs in five layers:

1. **Unit** tests in `packages/*/tests` (pytest).
2. **Tool contracts**: a JSON-schema snapshot and hashed golden outputs per MCP tool (`packages/mcp/contracts`), checked by pytest.
3. **End to end under `fake`**: the whole graph through the three gates, the challenger and remediation loop included, in pytest (the Polish run is marked slow).
4. **The eval harness** (`packages/evals`, the `modelforge-evals` command): drives a brief through the real graph with scripted gate decisions and measures retrieval, the pipeline, the challenger and cost against a baseline.
5. **Provider parity**: the results table in "Running against a real model", one row per profile.

The LLM judge of document quality is deferred: the `judge` purpose is routed in every profile but nothing calls it yet.

**Configuration.** `evals/evals.yml` names the briefs (`synthetic`, `polish`), the scripted approvals (alice approves the scope, bob accepts the model and approves the sign-off; when the sign-off rule refuses `approve`, the harness records `approve_with_conditions` with the refusal as its condition) and one entry per profile: the brief it runs, its layers (`retrieval`, `pipeline`, `challenger`, `cost`) and how its report is compared. `fake` runs the synthetic brief and compares every metric exactly except timing; `default` and `ci-cheap` run the Polish brief and compare only the metrics they list, each with a tolerance (a negative one is the largest allowed fall from the baseline, a positive one the largest allowed rise), plus absolute budgets on cost (8 and 5 USD) and wall time (2700 s).

**Goldens.** `evals/goldens/retrieval.yaml` holds 26 questions, each naming a corpus document and a phrase: a question counts as found when one of the top five hits comes from that document and contains the phrase. The knowledge unit test reads the same file. `evals/goldens/defects.yaml` is the planted-defect suite, six classes, each a mutation of the completed run's rendered document and the finding it should provoke:

| Class | Mutation | Expected finding |
| --- | --- | --- |
| `removed_section` | drop the monitoring section | key `missing_section:monitoring` |
| `swapped_number` | change one digit of a grounded number in the performance section | key `reverify:performance` |
| `claimed_absent_test` | append a sentence claiming an independent bootstrap stability test was performed and passed | category `performance`, evidence naming `performance` |
| `miscited_regulation` | replace the first passage marker in the regulatory context section with a passage of the IFRS 9 summary | key `miscited_regulation:regulatory_context:*` |
| `undiscussed_breach` | remove the performance sentences that cite a failing or warning test | key `undiscussed_test:*` |
| `missing_limitation` | remove the limitations sentences that mention a proxy | category `data`, evidence naming `limitations` |

Each case runs the real `challenge` node on a probe run of its own (requester `evals`, the base run's profile and plan, the mutated document, its recomputed completeness and copies of the artifacts the document cites, remediation bound 0), so the probe's calls, findings and events never touch the base run. A probe is marked completed without a manifest and is never replayed. A finding matches a case by its pre-check key (a glob) or, failing that, by category with the section among its evidence. Recall per class is 1 or 0, and not applicable when the mutation changes nothing; `defect_recall` is the mean over the applicable classes. On applicable probes, a finding that matches neither its case nor a finding the base run already has (by key, else by category and title) is a false positive, and `defect_precision` is matched findings over matched plus false positives.

**Metrics.** `retrieval_recall_at_5`; from the pipeline run, `pipeline_status`, `signoff_decision`, `grounding_pass_rate` (1 minus flagged sections over sections), `n_flagged_sections`, `n_ungrounded_numbers`, `completeness_complete`, `scope_exact_match`, `challenge_rounds`, `remediation_rounds`, `n_findings` and findings by source, severity and status, `n_sections_regenerated`; from the probes, `defect_recall`, the per-class recalls, `defect_precision` and `probe_cost_usd`; and from the run's `llm_calls` and `steps` rows, `cost_usd` in total and per purpose, `n_llm_calls`, `n_schema_retries`, input and output tokens, latency percentiles and wall time in total and per node.

**Reports and baselines.** Each run's report (no timestamps; it also names the models, the prompt hashes and the orchestrator version) is stored as an `eval_report` artifact on the pipeline run and written to `evals/reports/<profile>-<run_id>.json` (git-ignored); a Markdown summary goes to stdout and, when `GITHUB_STEP_SUMMARY` is set, to the job summary. Baselines are committed per profile in `evals/baselines/<profile>.json`.

```bash
make evals                       # fake profile, compared with evals/baselines/fake.json
make evals-live PROFILE=default  # a live profile against its committed baseline (needs the key)
make evals-baseline              # rewrite evals/baselines/fake.json from a fresh fake run
uv run modelforge-evals run --profile <name> [--brief <key>] [--layers retrieval,pipeline,...] \
    [--baseline PATH] [--write-baseline PATH] [--report-dir DIR] [--json]
uv run modelforge-evals compare <report.json> <baseline.json> --profile <name>
```

The harness needs a migrated database, like `modelforge run`. `--write-baseline` with `--baseline` writes the new baseline only when nothing regressed. Exit codes: 0 clean; 1 a regression against the baseline or a budget exceeded; 2 a configuration error (bad `evals.yml`, unknown profile, brief or layer, `challenger` without `pipeline`, an unreadable baseline), a pipeline run that parked, or any other failure while the layers ran. The baseline is read before any layer runs, so a missing one fails before anything is spent.

**CI.** `ci.yml` runs `make migrate` and `make evals` after `make test`, offline under `fake`. `.github/workflows/evals.yml` runs the `ci-cheap` profile against `evals/baselines/ci-cheap.json` on push to `main`, nightly at 03:00 UTC and on a pull request labelled `run-evals`, with a Postgres service, `ANTHROPIC_API_KEY` from the repository secrets and a cost cap of 8 USD (`MODELFORGE_COST_CAP_USD`), which applies to the pipeline run and to each probe run separately, since every probe is a run of its own carrying the base run's cap; the live profiles also budget `probe_cost_usd`, the probes' total (2 USD under `default`, 1 USD under `ci-cheap`), and the workflow uploads the reports. It needs that secret and a committed `ci-cheap` baseline; without the baseline it exits 2 before running anything. The project has no remote yet, so the workflow is committed but has not run.

**Results under `fake`** (the synthetic brief, from `evals/baselines/fake.json`):

| Metric | Value |
| --- | --- |
| Pipeline | completed, signed off with `approve` |
| Findings | 5 (3 pre-check, 2 review: 4 closed, 1 disputed; 4 medium, 1 low) |
| Rounds (challenge / remediation) | 2 / 1 |
| Sections regenerated | 2 |
| LLM calls | 35 (0 schema retries), cost 0 |
| Grounding pass rate | 1.0 (0 of 11 sections flagged, 0 ungrounded numbers) |
| Retrieval recall at 5 | 1.0 |
| Defect recall | 0.8 (`removed_section`, `swapped_number`, `claimed_absent_test` and `miscited_regulation` 1.0; `missing_limitation` 0.0; `undiscussed_breach` not applicable) |
| Defect precision | 1.0 |

The three pre-check findings are the passing out-of-time and sensitivity results the scripted document leaves unreported (`disc.ks.oot`, `sens.signs`, `sens.monotonic`), closed when the performance section was regenerated. `undiscussed_breach` is not applicable on the synthetic document: removing the performance sentences that cite a failing or warning test changes nothing there. `missing_limitation` is missed because the scripted proxy finding needs a plan whose target names a proxy, and the synthetic brief's does not. Like the rest of `fake`, these numbers show that the harness measures what it should, not how well a model reviews.

## Running against a real model

Each live profile runs the same governed sequence; only the profile changes.

```bash
# Anthropic: ANTHROPIC_API_KEY in .env
MODELFORGE_PROFILE=default uv run modelforge run examples/briefs/polish-pd.yaml --requester alice

# Ollama on localhost:11434
ollama pull gpt-oss:20b
MODELFORGE_PROFILE=local-ollama uv run modelforge run examples/briefs/polish-pd.yaml --requester alice

# LM Studio serving google/gemma-4-26b-a4b-qat on port 1234
MODELFORGE_PROFILE=local-lmstudio uv run modelforge run examples/briefs/polish-pd.yaml --requester alice
```

Then approve the three gates as in "Run a governed model build". If the challenger leaves a critical or high finding open or disputed, the sign-off gate refuses `approve` and names the findings; sign off with `approve_with_conditions` or `reject` instead. `MODELFORGE_OLLAMA_MODEL` and `MODELFORGE_LMSTUDIO_MODEL` run a different local model under the same profile, and the base URL variables point at another host. Ollama runs with a 32768-token context window unless `MODELFORGE_OLLAMA_NUM_CTX` sets another; pick one the model and the machine's memory support.

Every provider returns structured output through its integration's `json_schema` method by default, Anthropic included (forced tool calling is unsupported when thinking is enabled). A route's `structured` key overrides that: the Anthropic profiles set `structured: function_calling` on the `scope` route, because the modeling-plan schema exceeds the structured-output grammar's complexity limit, while the document routes keep `json_schema`. A section-writing call may use up to 8192 output tokens and a query-planning call up to 1024; on a thinking model the thinking tokens count against that limit.

Results on the Polish brief through the three gates, on one machine (Apple Silicon, 48 GB), with the deterministic artifacts identical across every run (out-of-time AUC 0.874, overall validation `fail`, 13 features). The `fake` row's findings, rounds, regenerated sections and LLM calls were measured on 10 November 2026 after the final-review fixes (its wall time at commit `5a11843`), with the challenger and the remediation loop; the `default` and `ci-cheap` rows for this branch are measured through the eval harness after it lands. The four rows below them were measured on 10 November 2026 at commit `00e0c6b` and predate the challenger and the remediation loop, so their findings, rounds and regenerated-section columns read n/a:

| Profile | Model | Outcome | Flagged sections | Ungrounded numbers | Findings (pre-check / review) | Rounds (challenge / remediation) | Sections regenerated | LLM calls | Cost (USD) | Wall time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fake` | `scripted-v1` | completed; signed off with `approve` | 0 of 11 | 0 | 9 (7 / 2): 8 closed, 1 disputed; max severity high, max open severity low | 2 / 1 | 2 (performance, monitoring) | 39 | 0 | 43 s |
| `default` | `claude-opus-5-5` | pending (measured by the controller after this task) | pending | pending | pending | pending | pending | pending | pending | pending |
| `ci-cheap` | `claude-sonnet-5-5` | pending (measured by the controller after this task) | pending | pending | pending | pending | pending | pending | pending | pending |
| `default` (before the challenger) | `claude-opus-5-5` | completed; document rendered and sealed | 1 of 11 | 0 | n/a | n/a | n/a | 31 (4 sections regenerated once) | 2.03 | 10 min 30 s |
| `local-ollama` (before the challenger) | `gpt-oss:20b` | completed; document rendered with banners | 10 of 11 | 159 | n/a | n/a | n/a | 52 (9 schema retries) | 0 | 26 min 8 s |
| `local-lmstudio` (before the challenger) | `google/gemma-4-26b-a4b-qat` | parked in `needs_attention` at `document` (`provider`: output hit the 8192-token limit) | n/a | n/a | n/a | n/a | n/a | 6 | 0 | 3 min 28 s |
| `local-lmstudio` with `MODELFORGE_LMSTUDIO_MODEL=meta/muse-glimmer` (before the challenger) | `meta/muse-glimmer` | parked in `needs_attention` at `document` (`provider`: a planning call hit the 1024-token limit) | n/a | n/a | n/a | n/a | n/a | 9 | 0 | 33 min 5 s |

Under `fake` the seven pre-check findings are four undiscussed calibration, stability and benchmark tests (`cal.hl`, `cal.eo`, `stab.csi.max`, `bench.gap`) and three unreported passing out-of-time and sensitivity results (`disc.ks.oot`, `sens.signs`, `sens.monotonic`), all closed when the performance section was regenerated; the two review findings are the scripted monitoring finding, closed when the monitoring section was regenerated, and the scripted governance finding, which the scripted developer disputes. That dispute is low severity, so `approve` is allowed. Every number in the sealed document traces to an artifact. The `fake` findings are scripted, so this row shows the loop at work, not a review.

What the numbers mean for the earlier rows. The Anthropic run's one banner is the governance section, whose regenerated text still named SR 26-2 without a `[K:]` passage; every number in the document traces to an artifact. The Ollama model wrote fluent prose but did not follow the alias-marker rule: most of its "ungrounded numbers" are the digits of regulation names written in a form the checker's regulation-name pattern does not recognise, and its markers often misuse the path grammar, so ten sections carry the attention banner and the failure list. Gemma 4 on LM Studio never terminated its JSON for one writing call even with the larger budget, so the gateway recorded the provider error and the run parked; `resume` re-runs the node. These are measurements of the models under this prompt and checker, not of the data pipeline, which produced the same artifacts in every run.

## Compose

```bash
make up     # build the image and start Postgres and the worker
make down   # stop the stack
```

The image runs the `modelforge` CLI with `worker` as its default command and carries the templates, prompts, profiles and knowledge corpus. It defaults to the `fake` profile, so the stack runs offline; set `MODELFORGE_PROFILE` (and `ANTHROPIC_API_KEY` for `default` or `ci-cheap`) in the shell or `.env` before `make up` to use a real model. The worker service connects to the Compose Postgres, mounts `./artifacts` and `./data`, and executes runs submitted with `--detach`; `docker compose logs worker` shows its JSON logs, starting with a `worker.ready` line.

## Configuration

The orchestrator reads these environment variables (a `.env` file fills any that are unset; an empty gateway variable counts as unset):

| Variable | Default |
| --- | --- |
| `MODELFORGE_DATABASE_URL` | `postgresql://modelforge:modelforge@localhost:5432/modelforge` |
| `MODELFORGE_ARTIFACT_DIR` | `./artifacts` |
| `MODELFORGE_DATA_DIR` | `./data` |
| `MODELFORGE_TEMPLATES_DIR` | `./templates` |
| `MODELFORGE_PROFILE` | `default` |
| `MODELFORGE_MCP_COMMAND` | unset (`python -m modelforge_mcp`) |
| `MODELFORGE_TOOL_TIMEOUT_S` | `600` |
| `MODELFORGE_LOG_LEVEL` | `INFO` |
| `MODELFORGE_PROMPTS_DIR` | `./prompts` |
| `MODELFORGE_PROFILES_DIR` | `./profiles` |
| `MODELFORGE_KNOWLEDGE_DIR` | `./knowledge-base` |
| `ANTHROPIC_API_KEY` | unset |
| `MODELFORGE_ANTHROPIC_BASE_URL` | unset (the gateway never reads `ANTHROPIC_BASE_URL`) |
| `MODELFORGE_OLLAMA_BASE_URL` | `http://localhost:11434` |
| `MODELFORGE_OLLAMA_MODEL` | the profile's model |
| `MODELFORGE_OLLAMA_NUM_CTX` | `32768` |
| `MODELFORGE_LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` |
| `MODELFORGE_LMSTUDIO_MODEL` | the profile's model |
| `MODELFORGE_COST_CAP_USD` | `10` |
| `MODELFORGE_LLM_TIMEOUT_S` | `300` |

The brief carries the run's own settings. Besides the model fields it takes an optional `restricted_terms` list (see "Redaction") and `remediation_rounds`, the bound on the remediation loop (default 2, from 0 to 3; 0 means the challenger reviews once and the run goes straight to sign-off). The evals read `evals/evals.yml` (see "Evals").

## Commands

```bash
make lint       # ruff, ruff format, pyright, import-linter
make test       # full test suite
make test-fast  # skip tests marked slow
make example    # end-to-end example pipeline
make db-up      # start Postgres
make db-down    # stop the Compose stack
make migrate    # uv run modelforge migrate
make worker     # uv run modelforge worker
make goldens    # regenerate the golden artifacts
make evals      # eval harness under fake, compared with evals/baselines/fake.json
make evals-live PROFILE=default  # eval harness under a live profile against its baseline
make evals-baseline              # rewrite evals/baselines/fake.json
make up         # start Postgres and the worker in Compose
make down       # stop the Compose stack
```

The `modelforge` CLI's commands are `migrate`, `run`, `approve`, `status`, `resume`, `abort`, `replay`, `verify` and `worker` (see "Run a governed model build"); `status` adds a `findings:` line (count by status and the highest severity) once the challenger has run. The `modelforge-evals` CLI has `run` (a profile's layers, the report and the baseline comparison) and `compare` (a report file against a baseline file); see "Evals".

Tests marked `db` need Postgres and skip when it is unreachable; set `MODELFORGE_REQUIRE_DB=1` to make them fail instead (CI does).

## Datasets

- Polish companies bankruptcy data, UCI Machine Learning Repository, DOI 10.24432/C5F600, licensed CC BY 4.0.
- Taiwanese bankruptcy prediction, UCI Machine Learning Repository, DOI 10.24432/C5004D, licensed CC BY 4.0.

Both are corporate bankruptcy datasets, not retail credit data, so two proxies are documented and applied:

- **Bankruptcy as default.** The bankruptcy label stands in for the default flag of a PD model.
- **Horizon files as vintages.** The Polish data ships as five files by forecasting horizon (`1year` to `5year`); each file is treated as a vintage, so a held-out file gives an out-of-time split. The Taiwanese set has no time dimension at all, so it uses a stratified random split and every out-of-time test is reported as not applicable.

## Status

Sub-projects 1 to 4 complete: the core toolkit; the governed orchestrator with its lineage store and MCP servers; the LLM gateway, knowledge layer, and scope and documentation agents; and the independent challenger, the bounded remediation loop, the sign-off rule on blocking findings, and the eval harness. See the architecture spec at `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md`, the orchestrator spec at `docs/superpowers/specs/2026-11-09-sp2-orchestrator-design.md`, the gateway and agents spec at `docs/superpowers/specs/2026-11-09-sp3-gateway-knowledge-agents-design.md`, the challenger, remediation and evals spec at `docs/superpowers/specs/2026-11-10-sp4-challenger-remediation-evals-design.md`, and the implementation plans at `docs/superpowers/plans/2026-11-09-sp1-core-toolkit.md`, `docs/superpowers/plans/2026-11-09-sp2-orchestrator.md`, `docs/superpowers/plans/2026-11-09-sp3-gateway-knowledge-agents.md` and `docs/superpowers/plans/2026-11-10-sp4-challenger-remediation-evals.md`.
