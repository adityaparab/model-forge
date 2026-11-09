# ModelForge: Umbrella Architecture Spec

**Status:** approved in brainstorming, awaiting written review
**Date:** 2026-11-09
**Scope of this document:** the whole system. It fixes the contracts between six sub-projects. Each sub-project gets its own short spec and implementation plan immediately before it starts. Sub-project 1 is specified here in enough detail to plan directly.

---

## 1. Purpose and success criteria

ModelForge is a governed, multi-agent copilot for the credit risk model development lifecycle. Given a raw credit dataset and a modeling brief, it produces a validated probability-of-default (PD) model, an SR 11-7 style model development document, an independent "effective challenge" review, a tamper-evident audit trail, and a model inventory entry, with human approval gates at the points where a bank would require them.

It is a reference implementation and portfolio project. It is not a production system, and the documentation says so.

Success criteria:

1. A reviewer clones the repo and runs one command. Within minutes a demo run is visible in the console, paused at the first approval gate.
2. Every number in the generated development document traces to a hashed artifact. Every regulatory claim traces to a knowledge passage.
3. The challenger agent is structurally isolated from the developer agent, and a test proves it.
4. Every non-LLM step is reproducible: a replay of a sealed run yields byte-identical artifact hashes.
5. The eval harness runs in CI and fails a pull request when document quality, challenger recall, retrieval recall, or cost regress.
6. The whole pipeline runs offline on a local model, and the README publishes the measured quality gap against the hosted model.
7. A senior reviewer can read the entire stack in an afternoon.

## 2. Regulatory framing (verified 2026-11-09)

- **SR 11-7** (Federal Reserve, 2011) was the model risk management standard for fifteen years. Its core ideas, conceptual soundness, effective challenge, independent validation, ongoing monitoring, and documentation sufficient for a third party to reproduce the model, shape this system's pipeline and document template.
- **SR 26-2** (Federal Reserve, 17 April 2026; OCC Bulletin 2026-13 is the parallel) supersedes SR 11-7 and SR 21-8. Its text explicitly places generative and agentic AI models **outside** its scope as "novel and rapidly evolving," while stating that a bank's broader risk management and governance practices should still govern them. An interagency request for information on generative and agentic AI governance has been signalled without a date.
- **Framing used throughout:** the regulator retired SR 11-7 and left agentic systems in a governance gap. ModelForge shows what effective challenge, lineage, and human accountability look like when applied to an agentic system anyway.
- **Do not claim** that SR 26-2 "extends SR 11-7 to LLMs" or names LLM-as-judge or adversarial probing as validation techniques. Neither is in the primary text.
- CECL (ASC 326), IFRS 9, and Basel IRB PD/LGD/EAD definitions are in the knowledge corpus as context for the documentation agent, not as compliance claims.

## 3. Scope

**In scope**

- PD model development on tabular credit data where the obligors are companies (financial ratios as features), which gives the project its wholesale flavour.
- Champion: WoE-binned logistic regression rendered as a JSON scorecard. Challenger model: LightGBM.
- Validation: discrimination, calibration, stability, sensitivity, benchmark, threshold compliance.
- Documentation generation with grounding checks.
- Independent challenger agent with remediation loop.
- Lineage, artifact store, replay, model inventory, approval gates.
- FastAPI control plane serving a React console as an SPA.
- Eval harness with CI gating.
- Three LLM providers behind one gateway: Anthropic (default), Ollama, LM Studio.

**Out of scope**

- LGD and EAD models (the inventory and document template leave room for them; no implementation).
- Real authentication or SSO. Identity is a header plus a role map, documented as pluggable.
- Cloud deployment, Kubernetes, object storage services, message brokers, metrics dashboards.
- Rerankers, fine-tuning, embeddings from a hosted provider.
- Any claim of regulatory compliance.

## 4. System architecture

### 4.1 Repository layout

One uv workspace. Python pinned to 3.12. Node 20+ for the console.

```
<repo root>/                     # project name: modelforge
  pyproject.toml                 # uv workspace root; shared tool config
  .python-version                # 3.12
  Makefile                       # demo, up, down, test, eval, lint, ui-dev
  docker-compose.yml             # postgres (pgvector), jaeger, api, worker
  packages/
    core/          modelforge_core          deterministic credit toolkit; no LLM imports
    mcp/           modelforge_mcp           five MCP servers + shared call logging
    gateway/       modelforge_gateway       LLM interface; anthropic | ollama | lmstudio
    knowledge/     modelforge_knowledge     ingest, chunk, embed, retrieve, cite
    orchestrator/  modelforge_orchestrator  LangGraph graph, agents, state, gates, worker
    lineage/       modelforge_lineage       Postgres models, events, artifacts, inventory
    api/           modelforge_api           FastAPI control plane + SPA serving
    evals/         modelforge_evals         harness, goldens, judge rubrics, budgets
  ui/                            React + Vite + TypeScript console
  knowledge-base/                regulatory corpus (Markdown with front-matter)
  prompts/                       versioned prompt files per agent
  profiles/                      routing profiles (default, local-ollama, local-lmstudio, ci-cheap)
  templates/                     document templates
  docs/                          specs, decision records, MRM memo, threat model, primer
  .github/workflows/             ci.yml, evals.yml
```

Package dependency direction (enforced by import-linter contracts):

```
core  <-  mcp  <-  orchestrator  <-  api
core  <-  lineage  <-  orchestrator, api, mcp
gateway  <-  orchestrator, evals
knowledge  <-  mcp (knowledge server)
evals  ->  everything (test-only)
```

`core` may not import `anthropic`, `ollama`, `httpx`, `requests`, `langgraph`, `langchain*`, or `mcp`. Only `gateway` and `core.datasets.download` may import an HTTP client.

### 4.2 Runtime topology

Docker Compose, four services:

| Service | Image | Role |
|---|---|---|
| `postgres` | pgvector/pgvector:pg16 | LangGraph checkpoints, lineage tables, run queue, knowledge embeddings |
| `jaeger` | jaegertracing/all-in-one | OpenTelemetry trace backend and UI |
| `api` | modelforge (entrypoint `api`) | FastAPI control plane; serves the built console as an SPA from `/` |
| `worker` | modelforge (entrypoint `worker`) | claims queued runs, executes the LangGraph graph, launches MCP servers as stdio subprocesses |

`api` and `worker` are one image with different entrypoints. The worker claims runs with `SELECT ... FOR UPDATE SKIP LOCKED`; there is a real queue and no broker.

Each MCP server also runs standalone (`uv run modelforge-mcp <server>`), so a reviewer can attach Claude Desktop or Claude Code to the credit-modeling server and call tools by hand.

Local LLM providers (Ollama, LM Studio) run on the host, not in Compose. The worker reaches them at configurable base URLs.

### 4.3 Console serving

Default: the API serves the production build of the console from `/` as a single-page application, with client-side routing falling back to `index.html`. API routes live under `/api/v1`. The Vite dev server is opt-in (`make ui-dev`) and proxies `/api` to the running API. There is no separate console container.

### 4.4 One command

`make demo`:

1. `docker compose up -d --build`
2. Wait for Postgres, run migrations, ingest the knowledge base if empty.
3. Download the default dataset (Polish companies bankruptcy) with SHA-256 verification; cache under `data/`.
4. Submit a demo run from `examples/briefs/polish-pd.yaml`.
5. Open the console at `http://localhost:8000`.

Provider selection is one environment variable: `MODELFORGE_PROFILE=default|local-ollama|local-lmstudio|ci-cheap`.

## 5. Run lifecycle

### 5.1 Inputs

- **Modeling brief** (YAML or Markdown with front-matter): model name, portfolio, target definition, intended use, constraints, requested thresholds, regulatory scope.
- **Dataset reference**: adapter name plus optional source override.

Both are hashed on intake. Hashes appear in the manifest.

### 5.2 Nodes

| # | Node | Kind | Does |
|---|---|---|---|
| 1 | `intake` | deterministic | validate brief schema, register run, create inventory stub, hash inputs |
| 2 | `scope` | LLM (purpose `scope`) | brief + modeling standards summary -> typed `ModelingPlan` via structured output |
| 3 | `gate_scope` | human | approve / edit plan / reject |
| 4 | `profile` | tool | `profile_dataset`, `check_leakage`, `compute_psi` |
| 5 | `split` | tool | `make_splits` (train / validation / out-of-time by vintage), seeded, row-hash manifest |
| 6 | `features` | tool | `bin_features_woe`, `select_features` |
| 7 | `build` | tool | `fit_logistic_pd` (champion), `fit_gbm_challenger`, `calibrate`, `score` |
| 8 | `validate` | tool | `discrimination_metrics`, `calibration_tests`, `stability_tests`, `sensitivity_analysis`, `benchmark_models`, `evaluate_thresholds` -> `ValidationReport` |
| 9 | `gate_model` | human | accept / adjust and loop to `features` or `build` (bounded) / reject |
| 10 | `document` | LLM (purpose `document`) | section-by-section development document with artifact and passage citations; grounding post-checks; `render_document` |
| 11 | `challenge` | LLM (purpose `challenge`) | deterministic completeness pre-checks, then independent review -> `FindingsList` |
| 12 | `remediate` | LLM (purpose `remediate`) + tool | developer responses to findings; re-render affected sections; challenger re-review of deltas; bounded rounds |
| 13 | `gate_signoff` | human | approve / approve with conditions / reject; sets inventory status |
| 14 | `seal` | deterministic | final manifest with every hash, version, seed, prompt hash; inventory entry finalised |

Loop bounds (defaults, configurable in the plan): `gate_model` adjustments 3; remediation rounds 2.

### 5.3 State

`RunState` is a Pydantic model. It holds references, never payloads.

```
run_id, status, profile_name
brief_ref, dataset_ref
plan: ModelingPlan | None
refs: profile, leakage, psi, split, binning, selection, champion, challenger_model,
      calibration, scores{train,val,oot}, validation, document, findings, responses
gates: {scope, model, signoff}: GateDecision | None
counters: {build_loops, remediation_rounds}
cost_usd, errors: list[ErrorRecord]
```

Checkpointer: `langgraph-checkpoint-postgres`. Gates: `interrupt()`; resumed by `POST /api/v1/runs/{id}/approvals`.

`ModelingPlan` fields: `model_name, portfolio, target{event, horizon}, vintage_policy{oot_vintages}, feature_policy{exclusions, max_features, iv_min, iv_max, corr_max}, methodology{champion: woe_logistic, challenger: lightgbm}, thresholds{auc_min, ks_min, gap_max, psi_warn, psi_fail, calibration_alpha, benchmark_gap}, regulatory_scope[], intended_use, tier`.

### 5.4 Error handling

- Nodes are idempotent given their input state. Re-running a node overwrites its refs.
- Tool errors are typed (`ToolError{server, tool, version, code, message}`), logged as events, retried with exponential backoff up to 3 attempts, then the run parks in `needs_attention`.
- LLM output failing schema validation is retried twice with the validation error appended, then parks the run in `needs_attention`.
- Provider switching is never automatic. The profile is fixed at run creation and recorded.
- Per-run cost cap (default 10 USD) and per-node timeouts. Breaching the cap pauses the run like a gate; a human can raise the cap and resume.
- `needs_attention` is visible in the console with the error record and a resume or abort action.

## 6. MCP tool contracts

Common rules for every tool:

- Pydantic input and output models; JSON schema published by the server.
- Semantic version per tool. Algorithm or output-schema change bumps the version. Eval goldens are pinned to tool versions.
- Seeded wherever randomness exists; the seed is an explicit input.
- Large outputs are written to the artifact store; the tool returns `ArtifactRef{id, kind, summary}`.
- Call log: `tool, version, input_hash, output_hash, duration_ms, run_id, step_id, artifact_ids`.
- Tools never accept filesystem paths from callers. They accept dataset references and artifact IDs.

### 6.1 data-profiler v1

| Tool | Input | Output |
|---|---|---|
| `profile_dataset` | dataset_ref | ProfileArtifact: row count, dtypes, missingness, descriptive stats, target rate overall and by vintage |
| `check_leakage` | dataset_ref | LeakageReport: single-feature AUC > 0.9 flags, perfect separation, duplicate rows, identifier-like columns |
| `compute_psi` | dataset_ref, base_vintages, compare_vintages, features? | PSIReport per feature |
| `make_splits` | dataset_ref, strategy (`vintage` or `stratified_random`), oot_vintages, val_share, seed | SplitManifest: row-hash lists per split; `oot_available` flag (false under `stratified_random`) |

### 6.2 credit-modeling v1

| Tool | Input | Output |
|---|---|---|
| `bin_features_woe` | dataset_ref, split_ref, features, method (quantile or tree), max_bins, min_bin_share, monotonic | BinningTables with WoE and IV per feature |
| `select_features` | binning_ref, iv_min, iv_max, corr_max | FeatureSelection with reasons per dropped feature |
| `fit_logistic_pd` | dataset_ref, split_ref, binning_ref, features, regularization | Scorecard (JSON: bins, WoE, coefficients, intercept, scaling) + coefficient table |
| `fit_gbm_challenger` | dataset_ref, split_ref, features, params, seed | GBMModel (LightGBM native text) |
| `calibrate` | model_ref, split_ref, method (platt, isotonic, none), target_rate? | CalibrationMap |
| `score` | model_ref, dataset_ref, split_ref?, calibration_ref? | Scores artifact (Parquet) |

### 6.3 validation v1

| Tool | Input | Output |
|---|---|---|
| `discrimination_metrics` | scores_refs, split_ref | AUC, Gini, KS per split with bootstrap confidence intervals |
| `calibration_tests` | scores_refs, split_ref, grades (default: 10 score deciles) | binomial test per grade, Hosmer-Lemeshow, expected vs observed |
| `stability_tests` | scores_refs, dataset_ref, split_ref | PSI on scores, CSI per feature |
| `sensitivity_analysis` | model_ref, dataset_ref, perturbations | coefficient sign checks, monotonicity, PD swing under bin shifts |
| `benchmark_models` | champion_scores, challenger_scores, split_ref | AUC/KS deltas with confidence intervals |
| `evaluate_thresholds` | validation_refs, plan_thresholds | ValidationReport: pass / warn / fail per test with evidence refs |

### 6.4 doc-renderer v1

| Tool | Input | Output |
|---|---|---|
| `list_template_sections` | template_id | sections with required artifact kinds and guidance |
| `render_document` | template_id, sections[], artifact_refs[], citations[] | Markdown, HTML, PDF artifacts |
| `check_completeness` | document_ref, template_id | missing sections, uncited claims, unresolved markers |

### 6.5 knowledge v1

| Tool | Input | Output |
|---|---|---|
| `search_regulation` | query, sources?, k | passages: citation_id, doc, section, text, score |
| `get_passage` | citation_id | passage |

## 7. LLM gateway

### 7.1 Interface

```
CompletionRequest:
  purpose: scope | document | challenge | remediate | judge
  system: str
  messages: list[Message]
  output_schema: type[BaseModel] | None
  effort: low | medium | high
  max_output_tokens: int
  cache_prefix: bool
  metadata: {run_id, step_id, section_id?}

CompletionResult:
  text: str | None
  parsed: BaseModel | None
  provider, model, prompt_name, prompt_version, prompt_hash
  usage: {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens}
  cost_usd, latency_ms, stop_reason, schema_valid
  raw_ref: ArtifactRef
```

Gateway pipeline: resolve route from profile by purpose -> redaction scan -> canonical JSON -> prompt hash -> adapter call -> schema validation -> usage and cost -> OTel span -> lineage `llm_calls` row -> result.

### 7.2 Adapters

- **Anthropic**: official `anthropic` Python SDK. Default model `claude-opus-5-5`. Adaptive thinking (parameter omitted). `output_config.effort` set per purpose. Structured output via the SDK parse helper with the Pydantic schema. Streaming with the final-message helper for document sections. `fallbacks: "default"` with the server-side fallback beta so a safety refusal does not crash a run; the fallback model is recorded. Prompt caching breakpoint after the stable prefix (system prompt, template guidance, modeling standards). Checks `stop_reason` for `max_tokens` and `refusal`.
- **Ollama**: `ollama` Python client, `chat` with `format` set to the JSON schema. Model name from profile.
- **LM Studio**: local OpenAI-compatible HTTP server via `httpx`, `response_format` of type `json_schema`. Model name from profile. No additional SDK.

### 7.3 Routing profiles

YAML under `profiles/`:

| Profile | scope | document | challenge | remediate | judge |
|---|---|---|---|---|---|
| `default` | opus-5-5 / low | opus-5-5 / high | opus-5-5 / high | opus-5-5 / medium | opus-5-5 / medium |
| `ci-cheap` | sonnet-5-5 / low | sonnet-5-5 / medium | sonnet-5-5 / medium | sonnet-5-5 / low | sonnet-5-5 / low |
| `local-ollama` | ollama model | ollama model | ollama model | ollama model | ollama model |
| `local-lmstudio` | lmstudio model | lmstudio model | lmstudio model | lmstudio model | lmstudio model |

Any purpose may be overridden per run, for example challenger on a different model than developer. Pricing table for cost accounting (USD per million tokens): opus-5-5 4 in / 20 out / 0.20 cache read; sonnet-5-5 2 / 10 / 0.20; haiku-5-5 0.10 / 0.50; local 0.

### 7.4 Prompts

`prompts/<agent>/<name>.v<N>.md` with front-matter (purpose, version, output schema name). Loaded by name and version. Hash recorded on every call. A prompt change is a new file; evals rerun.

### 7.5 Redaction

Structural: LLM inputs are tool summaries and artifact excerpts, never dataset rows. Column names are sanitised to `f_<n>` at ingestion with a dictionary artifact mapping to original names; the LLM sees sanitised names and a short, allow-listed description. Additionally, the gateway scans outbound text for field names marked restricted in the run config and for identifier-like patterns, and refuses to send with a typed error rather than masking silently.

## 8. Agents

### 8.1 Scope agent

One structured-output call at low effort. Inputs: brief, modeling standards summary. Output: `ModelingPlan`. The approver may edit any field at `gate_scope`; the diff is recorded.

### 8.2 Documentation agent

Section by section, never one call. Template sections (SR 11-7 style development document):

1. Executive summary
2. Model purpose and intended use
3. Regulatory and policy context
4. Data: sources, target definition, window, exclusions, quality, leakage checks, splits
5. Methodology and conceptual soundness: rationale, alternatives considered, challenger model, assumptions
6. Model development: feature engineering, selection, estimation, calibration, final specification
7. Performance and validation results
8. Limitations and weaknesses
9. Ongoing monitoring plan
10. Governance and lineage
11. Appendices: binning tables, scorecard, full metrics, citations

A deterministic map (`templates/<id>/sections.yaml`) lists which artifact kinds each section may cite. Each section takes two gateway calls, so no tool-use loop is needed and the pattern works identically on local models:

1. **Query planning** (structured output): given the section guidance and the artifact summaries, return up to 3 knowledge queries, or none. The orchestrator runs them through the knowledge MCP server.
2. **Writing** (structured output): given the guidance, the artifact excerpts the map allows, and the retrieved passages, return the section body in Markdown plus the list of citations used.

Citations are explicit markers: `[A:<artifact_id>#<path>]` and `[K:<citation_id>]`.

Post-checks per section:

- **Number grounding**: every numeric token in the text must match a value at the cited artifact path. Matching normalises both sides to 4 significant figures and treats percent and decimal forms as equivalent. Failing numbers are listed.
- **Citation validity**: every marker resolves.
- **Regulatory claims**: sentences naming a regulation or standard must carry a `[K:]` marker.

A failing section is regenerated once with the failure report appended. If it still fails, the section is rendered with a visible "REQUIRES AUTHOR ATTENTION" banner and the failure list. Nothing is silently dropped.

### 8.3 Challenger agent

- Own prompt file, own routing purpose.
- Context constructed only from lineage: rendered document, `ValidationReport`, `ModelingPlan`, and knowledge passages. Never any field of developer-side state, prompts, or intermediate generations. A test constructs a run with sentinel strings in developer state and asserts they cannot appear in the challenger request.
- Same two-call pattern as the documentation agent: a query-planning call returns up to 5 knowledge queries (for example, what SR 11-7 requires of a sensitivity analysis), the orchestrator runs them, then the review call receives document, report, plan and passages and returns the findings.
- Deterministic pre-checks: template completeness; required sections present (conceptual soundness, data, methodology, performance, limitations, ongoing monitoring); every `fail` or `warn` in the validation report discussed in the document; out-of-time and sensitivity results present or explicitly marked not applicable with reason.
- Output `FindingsList`: `id, round, severity (critical|high|medium|low), category (conceptual_soundness|data|methodology|performance|documentation|governance|limitations|monitoring), title, description, evidence[] (section ids, artifact ids, passage ids), required_action`.

### 8.4 Remediation

Developer agent receives findings plus its own artifacts. Per finding: `accept` with action, or `dispute` with artifact evidence. Accepted documentation findings trigger regeneration of the affected sections through the same post-checks. The challenger re-reviews only changed sections and responses. Maximum 2 rounds. Disputes remain visible at sign-off.

## 9. Knowledge layer

- **Corpus** under `knowledge-base/`. Federal Reserve letters SR 11-7 and SR 26-2 verbatim (US government works). CECL (ASC 326), IFRS 9 expected credit loss, and Basel IRB PD/LGD/EAD definitions as our own summaries with source URLs. `modeling-standards.md`: required document sections, default thresholds, mandatory validation tests. Front-matter per file: `source, authority, date, url, kind (primary|summary|internal)`.
- **Chunking**: by heading, about 500 tokens with 50-token overlap. Citation ID = short hash of (doc id, section path, chunk text), stable across re-ingestion.
- **Embeddings**: local, CPU, via `fastembed` with a small BGE model. Stored in pgvector.
- **Retrieval**: Postgres full-text search plus vector similarity, fused by reciprocal rank, top-k with optional source filter. No reranker.
- **Access**: only through the knowledge MCP server.
- **Eval**: `evals/goldens/retrieval.yaml` of question -> expected citation IDs; recall at 5.

## 10. Lineage and governance

### 10.1 Tables

| Table | Key columns |
|---|---|
| `runs` | id, created_at, requester, status, profile, brief_hash, dataset_ref, plan_ref, cost_usd, cost_cap_usd, trace_id, manifest_hash |
| `run_queue` | run_id, enqueued_at, claimed_by, claimed_at |
| `steps` | id, run_id, node, attempt, started_at, finished_at, status, input_state_hash, output_state_hash |
| `events` | id, run_id, step_id, ts, type, payload (jsonb), prev_hash, hash |
| `tool_calls` | id, step_id, server, tool, version, input_hash, output_hash, duration_ms, artifact_ids |
| `llm_calls` | id, step_id, purpose, provider, model, prompt_name, prompt_version, prompt_hash, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, latency_ms, stop_reason, schema_valid, raw_ref |
| `artifacts` | id (sha256), run_id, kind, media_type, size_bytes, uri, producer_step_id, schema_version |
| `approvals` | id, run_id, gate, decision, approver, comment, plan_diff, ts |
| `findings` | id, run_id, round, severity, category, title, description, evidence, required_action, status, response, response_evidence |
| `model_inventory` | model_id, run_id, name, version, owner, intended_use, tier, status, champion_ref, validation_summary, approved_at, approver, conditions, next_review |
| `prompts` | name, version, hash, path |

`events` is append-only, hash-chained (`hash = sha256(prev_hash || canonical(payload))`). `modelforge verify <run_id>` walks the chain and checks every artifact hash.

### 10.2 Artifact store

Content-addressed: `artifacts/<aa>/<bb>/<sha256>`. Interface `put(bytes, kind, media_type) -> ArtifactRef`, `get(id) -> bytes`, `uri(id)`. Filesystem implementation. S3 documented as the production path, not implemented.

### 10.3 No pickle

Champion PD model: JSON scorecard. Challenger model: LightGBM native text. Tables: Parquet. Reports: JSON. The repo has a test that fails if `pickle`, `joblib.dump`, or `cloudpickle` is imported anywhere outside tests.

### 10.4 Replay

`modelforge replay <run_id> [--rerun-llm]` re-executes deterministic nodes from the sealed manifest and asserts artifact hash equality. LLM nodes replay from stored `raw_ref` responses by default; with `--rerun-llm` they are re-executed and the diff is reported.

### 10.5 Four-eyes rule

`gate_model` and `gate_signoff` reject an approver equal to the run requester. Identity: `X-ModelForge-User` header mapped to roles in config. Documented as pluggable.

## 11. Observability

- OpenTelemetry Python SDK. Spans: run, node, tool call, LLM call. Attributes: run_id, step_id, tool, version, provider, model, tokens, cost.
- Exporter: OTLP to Jaeger. `trace_id` stored on `runs`; console links to the Jaeger trace.
- Logs: structured JSON via `structlog`, bound with run_id and step_id.
- Cost: accumulated per step and per run, shown live in the console.

## 12. API

Prefix `/api/v1`. All list endpoints paginate. Large data returns references.

| Method | Path | Purpose |
|---|---|---|
| POST | `/runs` | create run (brief, dataset_ref, profile) -> queued |
| GET | `/runs` | list |
| GET | `/runs/{id}` | state, steps, costs, gate status |
| GET | `/runs/{id}/events` | SSE stream with history replay (`Last-Event-ID`) |
| GET | `/runs/{id}/artifacts` | list |
| GET | `/artifacts/{id}` | download |
| GET | `/runs/{id}/document?format=html|md|pdf` | rendered document |
| GET | `/runs/{id}/findings` | findings and responses |
| POST | `/runs/{id}/approvals` | `{gate, decision, comment, plan?, conditions?}`; resumes the interrupt |
| POST | `/runs/{id}/resume` | resume from `needs_attention` (optionally raise cost cap) |
| GET | `/runs/{id}/manifest` | sealed manifest |
| POST | `/runs/{id}/replay` | enqueue a replay |
| GET | `/inventory`, `/inventory/{model_id}` | model inventory |
| GET | `/health`, `/config` | health; redacted effective config |

Non-API routes serve the SPA (section 4.3).

## 13. Console

React 18, Vite, TypeScript, TanStack Query, Tailwind, a small headless component set. SSE via `EventSource`.

Views: Runs; Run detail (fourteen-step stepper, live event feed, per-step cards for tool calls, LLM calls with cost, artifacts, trace link); Gates (editable plan form with diff; validation table of test, result, threshold, status; sign-off with findings, responses, decision, conditions); Document viewer (citation hover shows the artifact value or passage; grounding indicator per number); Findings board; Inventory; Artifacts.

Effort goes to the gates and the document viewer. The rest is clean and plain.

## 14. Eval harness and CI

### 14.1 Layers

1. **Unit** (`packages/*/tests`): golden fixtures with known WoE, AUC, PSI; property tests (hypothesis) for binning monotonicity, bin count invariants, scorecard round-trip.
2. **Tool contract** (`evals/contracts`): JSON-schema snapshot per tool; hashed golden output per tool version on a fixed sample. Output change without version bump fails.
3. **Pipeline integration** (`evals/integration`): full graph on a 2k-row fixture with LLM nodes replayed from recorded responses. No key required.
4. **LLM evals** (`evals/llm`, key required): scope agent exact-match on structured fields over golden briefs; document quality by LLM judge (per-section rubric, 1 to 5 with rationale, judge prompt and model recorded) plus deterministic grounding pass rate; challenger recall on a planted-defect suite (defect classes: removed section, swapped number, claimed-but-absent test, mis-cited regulation, undiscussed threshold breach, missing limitation) with recall per class and precision on extra findings; retrieval recall at 5; cost and latency budgets per run and per node.
5. **Provider parity**: layer 4 under `local-ollama` and `local-lmstudio`; results table published in the README.

Baselines: `evals/baselines/<profile>.json`. Regression beyond tolerance fails. Report written to the job summary.

### 14.2 Workflows

- `ci.yml` on pull request and push: ruff, pyright, import-linter, pytest layers 1 to 3 against a Postgres service container, console typecheck and build, Docker build. The workflow grows with the sub-projects: SP1 ships lint and unit tests only; Postgres, console and Docker steps arrive with SP2 and SP5.
- `evals.yml` on push to main, nightly, and on PR label `run-evals`: layer 4 under `ci-cheap` with `ANTHROPIC_API_KEY` secret, hard spend cap, baseline comparison, report upload.

## 15. Threat model

| Threat | Mitigation | Proof |
|---|---|---|
| Prompt injection via column names, values, or brief text | LLM never sees rows; columns sanitised to `f_<n>`; brief delimited as data; agents hold only the read-only knowledge tool | eval case with poisoned column name and brief |
| Exfiltration to external model | aggregates only; redaction gate; local profiles; import-linter rule on HTTP clients | lint contract; redaction unit tests |
| Tool misuse / parameter tampering | strict schemas, bounded ranges, artifact IDs not paths | schema tests |
| Deserialization | no pickle anywhere | import test |
| Audit tampering | hash-chained events; content-addressed artifacts; sealed manifest | `verify` command and test |
| Challenger contamination | structural context isolation; optional separate model | sentinel test |
| Cost runaway | per-run cap; bounded loops; node timeouts | integration test with low cap |
| Approval bypass | gates only resumable via approvals endpoint; four-eyes rule; identity recorded | API tests |
| Secrets exposure | env only; redacted config endpoint; never in lineage | config endpoint test |
| Supply chain | `uv.lock`; dataset SHA-256; pinned base images | checksum test |

## 16. Trade-off table: deterministic vs LLM

| Step | Choice | Why |
|---|---|---|
| Brief -> plan | LLM, structured output | Judgement over free text; output is typed and human-edited |
| Profiling, splits, binning, fitting, metrics | Deterministic | Must be reproducible and auditable; no judgement needed |
| Threshold evaluation | Deterministic | Policy, not opinion |
| Narrative documentation | LLM, grounded and checked | Writing is the judgement task; numbers are verified mechanically |
| Completeness checks | Deterministic first | Cheap, exact, frees the challenger for substance |
| Effective challenge | LLM, isolated | Finding conceptual gaps is judgement; isolation is structural |
| Approvals | Human | Accountability cannot be delegated |

## 17. Build order and sub-project boundaries

Three weeks full-time. Each sub-project starts with a short spec and a plan, and ends demonstrable through the CLI or console.

| SP | Days | Delivers | Contract handed to the next SP |
|---|---|---|---|
| 1 | 1 to 4 | repo skeleton and tooling; `core` toolkit; dataset adapters; unit tests; `ci.yml` lint and test | `core` public functions and Pydantic artifact schemas; canonical dataset schema |
| 2 | 5 to 8 | `lineage` (schema, events, artifact store); data-profiler, credit-modeling, validation, doc-renderer servers; orchestrator deterministic nodes and gates with stubbed LLM nodes; worker and queue; CLI `run`, `approve`, `replay`, `verify`; Compose with Postgres | `RunState`; tool call log; MCP client wiring; CLI approvals |
| 3 | 9 to 11 | `gateway` with three adapters, profiles, prompts, redaction, cost; `knowledge` and knowledge server; corpus; scope agent; documentation agent with grounding checks | `CompletionRequest/Result`; citation markers; document artifacts |
| 4 | 12 to 15 | challenger agent and pre-checks; remediation loop; eval harness layers 1 to 5; `evals.yml`; baselines | findings and responses tables; eval reports |
| 5 | 16 to 18 | `api`; SSE; SPA serving; console; OpenTelemetry and Jaeger | the demo |
| 6 | 19 to 21 | README; architecture diagram; MRM-style design memo; threat model doc; trade-off and cost tables; domain primer; demo video; fresh-clone test of `make demo` | the public repo |

Slip policy: SP5 compresses to gates and document viewer; SP6 drops the video to a scripted walkthrough.

### 17.1 Sub-project 1 detailed scope

**Repository**

- uv workspace root `pyproject.toml`; `.python-version` 3.12; `ruff`, `pyright`, `pytest`, `hypothesis`, `import-linter`, `pre-commit`; `Makefile` targets `lint`, `test`, `format`; `.github/workflows/ci.yml` running lint and tests; MIT `LICENSE`; README stub; `.gitignore` covering `data/`, `artifacts/`, `.env`, `notes/`.

**`packages/core/modelforge_core`**

- `schemas.py`: Pydantic models for every artifact kind named in section 6 (ProfileArtifact, LeakageReport, PSIReport, SplitManifest, BinningTables, FeatureSelection, Scorecard, GBMModelRef, CalibrationMap, Scores, DiscriminationMetrics, CalibrationTests, StabilityTests, SensitivityResults, BenchmarkResults, ValidationReport, ModelingPlan, thresholds).
- `datasets/`: `base.py` (adapter protocol, canonical schema `obligor_id, vintage, default_flag, f_1..f_n`, column dictionary), `download.py` (HTTP fetch with SHA-256 pin, cache), `polish.py` (UCI Polish companies bankruptcy, ARFF parsing, five horizon files mapped to `vintage` 1 to 5 with the proxy documented; the example brief trains and validates on vintages 1 to 4 and holds out vintage 5 as out-of-time), `taiwan.py` (UCI Taiwanese bankruptcy, CSV, no time column: pseudo-vintage by stratified random assignment, `oot_available=False`), `registry.py`.
- `profiling.py`, `leakage.py`, `psi.py`, `splits.py`.
- `binning.py` (WoE and IV; quantile and tree-based binning; monotonic enforcement; missing as its own bin), `selection.py`.
- `logistic.py` (WoE-logistic fit; coefficient table; conversion to `Scorecard`), `scorecard.py` (apply scorecard to a frame; points scaling), `gbm.py` (LightGBM fit and native text export), `calibration.py`, `scoring.py`.
- `metrics.py` (AUC, Gini, KS with bootstrap CIs), `calibration_tests.py` (binomial per grade, Hosmer-Lemeshow, expected vs observed), `stability.py` (PSI, CSI), `sensitivity.py`, `benchmark.py`, `thresholds.py` (ValidationReport builder with default thresholds from a `standards.py` constants module mirroring `knowledge-base/modeling-standards.md`).

Default thresholds (configurable per plan): AUC on out-of-time >= 0.70 pass, 0.65 to 0.70 warn; KS >= 0.25; train minus out-of-time AUC gap <= 0.05; PSI < 0.10 pass, 0.10 to 0.25 warn, > 0.25 fail; CSI same bands; binomial test at 5 percent per grade; Hosmer-Lemeshow p > 0.05 else warn; IV kept in [0.02, 0.5] with > 0.5 flagged as suspicious; correlation pruning at |r| > 0.7; champion within 0.03 AUC of challenger model else warn.

**Tests**

- Golden fixtures: synthetic frame with known bins and WoE; constructed scores with known AUC and KS; shifted distributions with known PSI.
- Property tests: monotonic binning produces monotone WoE; bin counts sum to N; scorecard JSON round-trips and reproduces logistic probabilities.
- End-to-end core smoke test on a 5k-row Polish sample: profile, split, bin, fit, score, validate; asserts out-of-time AUC > 0.65 and a complete ValidationReport. Marked `slow`, still runs in CI.
- Import-linter contract: `core` imports none of the forbidden packages.

**Done when** `make lint test` passes locally and in CI, and a 30-line script in `examples/core_pipeline.py` produces a scorecard JSON and a ValidationReport from the Polish dataset.

## 18. Risks and open items

- **Schedule.** Three weeks is tight. Slip policy in section 17.
- **Dataset proxies.** Bankruptcy is a proxy for default; the Polish horizon files are a proxy for vintages; the Taiwanese set has no time dimension. All three are documented limitations and expected challenger findings.
- **LM Studio** is not installed yet. The adapter is written against the documented OpenAI-compatible endpoint and tested with a recorded fixture until the server is available.
- **Anthropic credentials** are not in the environment yet. SP3 cannot run live until they are. SP1 and SP2 need no key.
- **Local model quality.** Small local models may fail structured output or grounding checks more often. That is a measured result, not a defect, and goes in the parity table.
- **Python 3.15** on the host is too new for ML wheels; uv pins 3.12.

## 19. Glossary

- **PD / LGD / EAD**: probability of default, loss given default, exposure at default. The three Basel IRB risk parameters. This project builds PD only.
- **WoE / IV**: weight of evidence and information value. WoE transforms a binned feature into log-odds; IV summarises a feature's predictive power.
- **KS / Gini / AUC**: discrimination measures. Gini = 2 AUC - 1.
- **PSI / CSI**: population and characteristic stability indices; distribution shift of scores and features.
- **Out-of-time (OOT)**: a holdout from a later period than training; the honest test of a credit model.
- **Effective challenge**: SR 11-7's requirement that validation be performed by people with incentives, competence and influence independent of model development.
- **MRM**: model risk management, the second-line function that validates and approves models.
- **Model inventory**: the register of every model in use, its status, owner, and review schedule.
