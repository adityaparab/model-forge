# SP2: Lineage, MCP Servers, Orchestrator, Worker and CLI

**Status:** approved 2026-11-09; implemented on branch `sp2-orchestrator`. Section 10 lists the as-built amendments made during execution (each is also a ruling in the SP2 hand-off note).
**Date:** 2026-11-09
**Parent:** `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` (umbrella spec), section 17 row 2 and sections 4.2, 5, 6, 10, 11
**Inputs:** `docs/superpowers/notes/2026-11-09-sp1-handoff.md`; `examples/core_pipeline.py` (the reference node sequence)

---

## 1. Purpose and done criteria

SP2 turns the deterministic toolkit from SP1 into a governed run. A run is created from a modeling brief, executed by a worker as a LangGraph state machine whose deterministic nodes call four MCP servers over stdio, paused at three human gates that a CLI approval resumes, and sealed into a manifest. Every artifact is content-addressed, every event is hash-chained, and a replay reproduces every deterministic artifact hash. The three LLM nodes and remediation are deterministic stubs returning fixture outputs; the LangChain gateway, knowledge layer and real agents arrive in SP3 and SP4.

Done when:

1. `make lint test` passes locally (with Postgres from Compose) and in CI (Postgres service container).
2. `modelforge run examples/briefs/polish-pd.yaml` creates a run, executes to `gate_scope`, and `modelforge approve` carries it through `gate_model` and `gate_signoff` to a sealed manifest and an inventory entry, from the CLI alone.
3. `modelforge replay <run_id>` re-executes the deterministic nodes and reports every artifact hash equal; `modelforge verify <run_id>` walks the event chain and re-hashes every artifact.
4. Each MCP server runs standalone (`uv run modelforge-mcp credit-modeling`) and can be called from an MCP client with nothing but an artifact directory and a data directory.
5. Tests at the SP1 standard: unit tests per module, contract snapshots per tool version, an integration run of the whole graph on the synthetic dataset with stubbed LLM nodes, and a slow test on the Polish brief.

Decisions fixed by the umbrella spec and not reopened here: Postgres on the pgvector image; the LangGraph Postgres checkpointer; gates as `interrupt()`; the run queue claimed with `SELECT ... FOR UPDATE SKIP LOCKED`; the content-addressed filesystem artifact store at `artifacts/<aa>/<bb>/<sha256>`; hash-chained append-only `events`; the tables of umbrella section 10.1; servers runnable standalone over stdio; no pickle; explicit seeds; `modelforge_core` LLM-free and network-free.

Decisions made in brainstorming (user approved 2026-11-09): servers are database-free and the orchestrator writes all lineage; migrations are plain numbered SQL files; the doc-renderer produces Markdown and HTML, PDF is deferred to SP5; the CLI executes in the foreground by default with `--detach` for a Compose worker; local Postgres comes from Docker Compose.

Verified against installed versions on 2026-11-09: `mcp` 2.3.0 (FastMCP renamed `MCPServer`; Pydantic-typed tools return `structured_content`; `Client(server_instance)` connects in-process; `ToolError` messages reach the client), `langgraph` 1.2.20, `langgraph-checkpoint-postgres` 3.2.0, `psycopg` 3.3.

## 2. Packages and dependency rules

Three new workspace members join `packages/core`:

```
packages/lineage/      modelforge_lineage       Postgres access, migrations, events, artifact store, runs, steps, queue, approvals, inventory, verify, logging
packages/mcp/          modelforge_mcp           four MCP servers, shared tool envelope, contracts, `modelforge-mcp` entrypoint
packages/orchestrator/ modelforge_orchestrator  RunState, graph, nodes, gates, MCP tool client, LLM stubs, worker, replay, CLI `modelforge`
```

Dependencies (in addition to core's):

| Package | Depends on |
|---|---|
| `modelforge_lineage` | `psycopg[binary]>=3.3`, `psycopg-pool>=3.3`, `structlog>=26`, `pydantic>=2.7`, `pyarrow>=16` (Parquet helpers) |
| `modelforge_mcp` | `modelforge-core`, `modelforge-lineage` (artifact store and logging only), `mcp>=2.3`, `jinja2>=3.1`, `markdown-it-py>=3`, `pyyaml>=6` |
| `modelforge_orchestrator` | `modelforge-core`, `modelforge-lineage`, `modelforge-mcp`, `langgraph>=1.2`, `langgraph-checkpoint-postgres>=3.2`, `typer>=0.28`, `pyyaml>=6` |

Import-linter contracts (root packages become the four `modelforge_*` packages):

- `modelforge_core` keeps its existing forbidden list (now also protecting against `psycopg`, `structlog`, `typer`).
- `modelforge_lineage` may not import `mcp`, `langgraph`, `langchain*`, `modelforge_mcp`, `modelforge_orchestrator`.
- `modelforge_mcp` may not import `langgraph`, `langchain*`, `psycopg`, `modelforge_orchestrator`. Servers never touch Postgres.
- No package imports `anthropic`, `openai`, `ollama`, `langchain_anthropic`, `langchain_openai`, `langchain_ollama`, `httpx` or `requests` (core's download module keeps its single exception).
- Layering: `core <- lineage <- mcp <- orchestrator`.

The no-pickle scan extends to every `modelforge_*` package.

## 3. Lineage package

### 3.1 Database access and migrations

Plain SQL through psycopg 3 (synchronous). No ORM. A `Database` object wraps a `psycopg_pool.ConnectionPool` built from `MODELFORGE_DATABASE_URL` and exposes `connection()` and `transaction()` context managers; every repository function takes a connection so a node's writes share one transaction. Pool connections are opened with `autocommit=True` and dict rows, which the LangGraph `PostgresSaver` requires, so lineage writes always use explicit `with conn.transaction():` blocks; the checkpointer is constructed on the same pool.

Migrations are numbered files `modelforge_lineage/migrations/NNNN_<name>.sql`, applied in order by `modelforge_lineage.migrate.apply(db)` and recorded in `schema_migrations(version int primary key, name text, applied_at timestamptz)`. The LangGraph checkpoint tables live in the same database but are created by the orchestrator (`modelforge_lineage` may not import `langgraph`): `modelforge migrate` applies the SQL migrations and then calls `PostgresSaver.setup()`; the worker does the same at start (both idempotent).

### 3.2 Tables

Umbrella 10.1 with the SP2 additions marked (+).

| Table | Columns |
|---|---|
| `runs` | `id text pk`, `created_at`, `updated_at`, `requester text`, `status text`, `profile text`, `brief_hash text`, `brief_ref text` (+ artifact id of the brief), `dataset_ref text`, `dataset_id text null` (+ dataset artifact id, set by the `profile` node), `plan_ref text null`, `seed int` (+), `cost_usd numeric default 0`, `cost_cap_usd numeric default 10`, `trace_id text null`, `manifest_hash text null`, `replay_of text null references runs(id)` (+), `error jsonb null` (+ latest ErrorRecord while `needs_attention`) |
| `run_queue` | `run_id text pk references runs`, `kind text check (kind in ('start','resume','replay'))` (+), `enqueued_at`, `claimed_by text null`, `claimed_at timestamptz null` |
| `steps` | `id text pk`, `run_id`, `node text`, `attempt int`, `started_at`, `finished_at null`, `status text check in ('running','succeeded','failed')`, `input_state_hash text`, `output_state_hash text null` |
| `events` | `id bigserial pk`, `run_id`, `step_id text null`, `ts timestamptz`, `type text`, `payload jsonb`, `prev_hash text`, `hash text`, `unique (run_id, hash)` |
| `tool_calls` | `id text pk`, `step_id references steps`, `server text`, `tool text`, `version text`, `input_hash text`, `output_hash text`, `duration_ms int`, `artifact_ids text[]`, `seq bigserial` (+ added by migration `0002`, gives insertion order) |
| `llm_calls` | as umbrella 10.1; created now, written from SP3 |
| `artifacts` | `id text` (sha256), `run_id text`, `kind text`, `media_type text`, `size_bytes bigint`, `uri text`, `producer_step_id text null`, `schema_version text`, `created_at`; `primary key (id, run_id)` because the same content may be produced by several runs |
| `approvals` | `id text pk`, `run_id`, `gate text`, `decision text`, `approver text`, `comment text`, `payload jsonb` (+ the full `GateDecision`, including an edited plan, so the worker can resume from the row), `plan_diff jsonb null`, `conditions text[] null` (+), `adjust_target text null` (+), `ts` |
| `findings` | as umbrella 10.1 (`id, run_id, round, severity, category, title, description, evidence jsonb, required_action, status, response, response_evidence jsonb`); the challenge and remediate stubs write it |
| `model_inventory` | `model_id text pk`, `run_id`, `name`, `version int`, `owner`, `intended_use`, `tier int`, `status text`, `champion_ref text null`, `validation_summary jsonb null`, `approved_at null`, `approver null`, `conditions text[] null`, `next_review date null` |
| `prompts` | `name, version, hash, path; primary key (name, version)`; created now, written from SP3 |
| `schema_migrations` | see 3.1 |

Run statuses: `queued`, `running`, `awaiting_scope`, `awaiting_model`, `awaiting_signoff`, `needs_attention`, `completed` (sealed after sign-off approval, with or without conditions), `rejected` (sealed after a reject at any gate), `aborted`. Every transition appends a `run.status_changed` event.

Identifiers are prefixed hex strings from `uuid4`: `run_<12 hex>`, `step_<12 hex>`, `tc_<12 hex>`, `apr_<12 hex>`. Artifact ids are the SHA-256 of the bytes.

### 3.3 Event chain

`append_event(conn, run_id, type, payload, step_id=None) -> EventRecord`. The body hashed is `{"type", "ts", "step_id", "payload"}` with `ts` an ISO-8601 UTC string at microsecond precision, so time and type are inside the chain (a deliberate widening of the umbrella's `canonical(payload)`); `hash = sha256(prev_hash + canonical(body))` with `canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. The genesis `prev_hash` is 64 zeros. Appends to one run are serialised by `SELECT ... FOR UPDATE` on the run's row, so the chain cannot fork.

Event types: `run.created`, `run.enqueued`, `run.claimed`, `run.status_changed`, `step.started`, `step.finished`, `tool.called`, `tool.failed`, `artifact.stored`, `gate.requested`, `gate.decided`, `gate.applied`, `intake.validated`, `profile.psi_skipped`, `error.recorded`, `run.sealed`, `replay.completed`, `worker.rejected_claim`.

`verify_chain(conn, run_id) -> ChainReport{events: int, ok: bool, first_bad_id: int | None}` recomputes every hash in id order.

### 3.4 Artifact store

`ArtifactStore(root: Path)` is pure filesystem, content-addressed at `root/<aa>/<bb>/<sha256>`; writes go to a temporary file in the same directory and are renamed into place, so a partial write never appears under a valid id, and writing existing content is a no-op.

```
put(data: bytes, *, kind: str, media_type: str, producer: Producer | None = None, summary: dict = {}) -> ArtifactRef
get(id: str) -> bytes            # ArtifactNotFound if absent
uri(id: str) -> str              # file:// URI
exists(id: str) -> bool
put_model(model: BaseModel, *, kind, ...) -> ArtifactRef   # model_dump_json(indent=2), media application/json
get_model(id, model_type) -> BaseModel
put_frame(frame, *, kind, ...) -> ArtifactRef              # Parquet via pyarrow, media application/vnd.apache.parquet
get_frame(id) -> pd.DataFrame
```

`ArtifactRef(StrictModel)`: `id`, `kind`, `media_type`, `size_bytes`, `producer: Producer{server, tool, version} | None`, `summary: dict[str, Any]`. The summary carries a few headline values (row counts, AUC by split, overall status) so later nodes and the console can show results without loading the artifact.

Artifact kinds and media types: `brief` (application/yaml), `dataset` (Parquet), `column_dictionary`, `profile`, `leakage`, `psi`, `split_manifest`, `binning_tables`, `feature_selection`, `scorecard`, `gbm_model`, `calibration_map`, `discrimination`, `calibration_tests`, `stability`, `sensitivity`, `benchmark`, `validation_report`, `modeling_plan`, `document_sections`, `completeness`, `findings`, `responses`, `manifest`, `replay_report` (all application/json), `scores` (Parquet), `document_md` (text/markdown), `document_html` (text/html). The `gbm_model` artifact is the `GBMModel` Pydantic JSON, whose `model_text` field is the LightGBM native text; the features list and parameters it carries are needed to score, and nothing is pickled.

Rule: no artifact contains a wall-clock value. Timestamps live in lineage rows. This is what makes replay hashes comparable.

Parquet determinism: `put_frame` writes with fixed options (pyarrow, `compression="zstd"`, `write_statistics=True`, no row-group size override) and the test suite asserts that writing the same frame twice yields identical bytes.

The `artifacts` table row is inserted by the orchestrator when it receives a ref (section 5.3); standalone server use writes files only.

### 3.5 Run queue

`enqueue(conn, run_id, kind)` upserts the queue row and appends `run.enqueued`. `claim(conn, worker_id) -> QueuedRun | None`:

```sql
UPDATE run_queue SET claimed_by = %(worker)s, claimed_at = now()
WHERE run_id = (SELECT run_id FROM run_queue WHERE claimed_by IS NULL
                ORDER BY enqueued_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING run_id, kind;
```

`release(conn, run_id)` deletes the row when the worker finishes with the run (sealed, parked at a gate, or parked in `needs_attention`). A test runs two claimers concurrently against two queued runs and asserts each claims a different run.

### 3.6 Approvals and the four-eyes rule

`record_approval(conn, run_id, decision: GateDecision) -> ApprovalRecord` validates that the run is `awaiting_<gate>` (`GateMismatchError`), that for gates `model` and `signoff` the approver differs from `runs.requester` (`FourEyesError`), that an `adjust` decision does not exceed the loop bound (`LoopBoundError`), stores the row (with `plan_diff` as a list of `{path, old, new}` when a plan was edited), appends `gate.decided`, and leaves enqueueing to the caller. Identity is a CLI argument in SP2; the API header mapping arrives in SP5.

### 3.7 Model inventory

`create_stub(conn, run_id, name, owner, intended_use, tier) -> model_id` with `status="draft"`, `version = 1 + count of inventory rows with that name`, `model_id = f"{name}@v{version}"`. `finalise(conn, model_id, status, champion_ref, validation_summary, approver, conditions, approved_at)` at seal; status is `approved`, `approved_with_conditions` or `rejected`.

### 3.8 Verify

`verify_run(conn, store, run_id) -> VerifyReport{chain: ChainReport, artifacts_checked: int, artifacts_missing: list[str], artifacts_corrupt: list[str], manifest_ok: bool, ok: bool}`: chain check; every `artifacts` row's file exists and re-hashes to its id; the manifest artifact exists and every ref it lists is in the `artifacts` table for that run. A test tampers with one event payload and one artifact file and asserts both are reported.

### 3.9 Logging

`modelforge_lineage.observability.configure_logging(level)` configures structlog to emit one JSON object per line to stderr (stdout is the MCP protocol channel in servers). Loggers are bound with `run_id` and `step_id` where known, and `server`/`tool` in servers. No tracing in SP2.

## 4. MCP servers

### 4.1 Common envelope

Every server is an `MCPServer` from `mcp.server.mcpserver` with `version` set to the server version. Every tool is registered with `@server.tool(name=..., description=..., meta={"version": "<semver>"})`, takes exactly one argument `request: <Tool>Input` (a `StrictModel`) and returns a `StrictModel`. Tools that produce artifacts return `ArtifactRef` or an output model whose `ArtifactRef`-typed fields are the produced artifacts; tools that produce only metadata (`list_template_sections`) return a plain model. Tool version starts at `1.0.0`; an algorithm or schema change bumps it and the contract test fails until the golden is regenerated.

Servers hold no database connection. Configuration is two environment variables read at start: `MODELFORGE_ARTIFACT_DIR` (default `./artifacts`) and `MODELFORGE_DATA_DIR` (default `./data`); the doc-renderer also reads `MODELFORGE_TEMPLATES_DIR` (default `./templates`). Tools never accept filesystem paths: inputs are artifact ids, `DatasetRef`s and template ids.

`DatasetRef(StrictModel)`: `adapter: str` (a registry name), `seed: int = 42` (used by adapters whose canonical frame depends on a seed, such as the Taiwanese pseudo-vintages and the synthetic adapter). String form `adapter` or `adapter@seed`.

Errors: a tool raises `mcp.server.mcpserver.exceptions.ToolError` whose message is the JSON of `ToolErrorPayload{code, message}`, with codes `invalid_input` (the request is well-formed but inconsistent, for example a feature missing from the binning tables), `artifact_not_found`, `dataset_unavailable` (download or checksum failure), `internal`. Input validation failures are produced by the SDK and arrive as `is_error` results. The client maps every `is_error` result, and any transport failure, into the typed `ToolError{server, tool, version, code, message}` of umbrella 5.4 (`code="transport"` or `"timeout"` for the latter).

Each tool call logs one structured line at start and end (`tool`, `version`, `duration_ms`, `artifact_ids`). The lineage `tool_calls` row is written by the client (section 5.3).

`modelforge_mcp.registry.TOOLS: dict[tuple[server, tool], ToolSpec{input_model, output_model, version}]` lists every tool; the orchestrator uses it to validate structured results, and the contract test uses it to snapshot schemas.

### 4.2 data-profiler 1.0.0

| Tool | Input | Output |
|---|---|---|
| `load_dataset` | `dataset_ref: DatasetRef` | `LoadDatasetOutput{dataset: ArtifactRef(dataset), dictionary: ArtifactRef(column_dictionary), oot_capable: bool, n_rows: int}`. Loads through `modelforge_core.datasets.registry.load_dataset` (downloading with the pinned SHA-256 if absent), stores the canonical frame as Parquet. The content-addressed store is the Parquet cache the SP1 reviewer asked for. |
| `profile_dataset` | `dataset_id, dataset_name: str | None` | `ArtifactRef(profile)`; summary `n_rows, n_features, target_rate`. `dataset_name` labels the artifact (the tool has no other source for a name); the artifact id is used when absent |
| `check_leakage` | `dataset_id, dataset_name: str | None, auc_threshold=0.9` | `ArtifactRef(leakage)`; summary `flagged: list[str], duplicate_rows` |
| `compute_psi` | `dataset_id, base_vintages, compare_vintages, features: list[str] | None, n_bins=10` | `ArtifactRef(psi)`; summary `max_psi, worst_feature` |
| `make_splits` | `dataset_id, strategy, oot_vintages, val_share=0.2, seed` | `ArtifactRef(split_manifest)`; summary `rows: {train, val, oot}, oot_available` |

### 4.3 credit-modeling 1.0.0

| Tool | Input | Output |
|---|---|---|
| `bin_features_woe` | `dataset_id, split_id, fit_split="train", features, method="quantile", max_bins=10, min_bin_share=0.05, monotonic=True` | `ArtifactRef(binning_tables)`; summary `n_features, iv: {feature: iv}` |
| `select_features` | `dataset_id, split_id, binning_id, fit_split="train", iv_min, iv_max, corr_max, max_features: int | None` | `ArtifactRef(feature_selection)`; summary `selected: list[str], n_dropped` |
| `fit_logistic_pd` | `dataset_id, split_id, binning_id, selection_id, model_name, fit_split="train", stepwise=True, penalty="l2", c=1.0` | `FitLogisticOutput{scorecard: ArtifactRef(scorecard), selection: ArtifactRef(feature_selection)}`. With `stepwise`, the wrong-sign drops are merged into a new `FeatureSelection` artifact (the example's merge, done once here); `Scorecard.fit_method` records the method. Summary `features, intercept, n_wrong_sign_drops` |
| `fit_gbm_challenger` | `dataset_id, split_id, features, model_name, seed, params: dict | None, fit_split="train", val_split="val", early_stopping_rounds=30` | `ArtifactRef(gbm_model)`; summary `num_trees, features` |
| `calibrate` | `model_id, dataset_id, split_id, fit_split="val", method="platt"` | `ArtifactRef(calibration_map)`; summary `method, platt_a, platt_b` |
| `score` | `model_id, dataset_id, split_id, calibration_id: str | None` | `ArtifactRef(scores)` (Parquet with `SCORES_COLUMNS`); summary `rows: {split: n}`. `model_id` may be a scorecard or a gbm_model artifact; `predict_model` in core dispatches on the parsed type |

### 4.4 validation 1.0.0

| Tool | Input | Output |
|---|---|---|
| `discrimination_metrics` | `scores_id, model_name, bootstrap_samples=200, seed` | `ArtifactRef(discrimination)`; summary `auc: {split: value}` |
| `calibration_tests` | `scores_id, split, alpha=0.05, n_grades=10` | `ArtifactRef(calibration_tests)`; summary `n_grades, grades_failed, hl_p_value, eo_ratio` |
| `stability_tests` | `scores_id, dataset_id, binning_id, features, base_split="train", compare_split, n_bins=10, thresholds: Thresholds = Thresholds()` | `ArtifactRef(stability)`; summary `score_psi, max_csi`. The plan's thresholds set the stored PSI/CSI bands |
| `sensitivity_analysis` | `model_id (scorecard), binning_id, dataset_id, shifts=[-1, 1]` | `ArtifactRef(sensitivity)`; summary `all_signs_ok, all_monotone` |
| `benchmark_models` | `champion_scores_id, challenger_scores_id, champion, challenger, gap_threshold, bootstrap_samples=200, seed` | `ArtifactRef(benchmark)`; summary `decided_on, auc_delta, champion_within_gap` |
| `evaluate_thresholds` | `model_name, thresholds: Thresholds, discrimination_id, calibration_id, stability_id, sensitivity_id: str | None, benchmark_id: str | None, selection_id, binning_id, oot_available` | `ArtifactRef(validation_report)`; summary `overall, statuses: {test_id: status}` |

### 4.5 doc-renderer 1.0.0

Templates live under `templates/<template_id>/`: `sections.yaml` (ordered sections with `id`, `title`, `required`, `artifact_kinds`, `guidance`) and `document.md.j2` (Jinja2, StrictUndefined). SP2 ships `sr11-7-dev-doc` with the eleven sections of umbrella 8.2.

| Tool | Input | Output |
|---|---|---|
| `list_template_sections` | `template_id` | `TemplateSections{template_id, sections: list[SectionSpec]}` |
| `render_document` | `template_id, sections_id` (a `document_sections` artifact), `title`, `context: dict[str, str]` (model name, dataset; never run ids or times) | `RenderOutput{markdown: ArtifactRef(document_md), html: ArtifactRef(document_html)}`. Sections missing from the artifact render with a visible `REQUIRES AUTHOR ATTENTION` banner; HTML via markdown-it-py with a fixed, embedded stylesheet |
| `check_completeness` | `document_id (document_md), template_id` | `ArtifactRef(completeness)` holding `CompletenessReport{missing_sections, unresolved_markers, uncited_regulatory_sentences, complete}`. Markers are `[A:<artifact_id>#<path>]` and `[K:<citation_id>]`; in SP2 a marker is unresolved when malformed; sentences naming a regulation from a fixed list (SR 11-7, SR 26-2, OCC 2026-13, CECL, IFRS 9, Basel) without a `[K:]` marker are listed |

`DocumentSections(StrictModel)`: `template_id`, `sections: list[DocumentSection{section_id, body_md, citations: list[Citation{marker, kind: "A" | "K", target}]}]`. Lives in `modelforge_mcp.schemas` and is shared with the orchestrator's document stub and SP3's agent.

Citation path grammar (for `[A:<artifact_id>#<path>]`): path steps are joined by `.`; a step applied to a list of objects selects the element whose discriminator field equals the step, where the discriminator is `split` for `by_split`, `test_id` for `tests`, `feature` for feature lists and `grade` for `grades` (so `by_split.oot.auc` and `tests.disc.auc.oot.value` are valid). SP3's grounding resolver implements it. The HTML renderer runs with raw HTML disabled, so markup inside a section body is escaped.

PDF output is deferred to SP5 (recorded as a ruling).

### 4.6 Packaging and standalone use

`modelforge_mcp/__main__.py` exposes `modelforge-mcp <server>` with servers `data-profiler`, `credit-modeling`, `validation`, `doc-renderer`, running `server.run("stdio")`. `modelforge-mcp contracts --out <dir>` writes the JSON schema and version of every tool. A reviewer attaches an MCP client with command `uv run modelforge-mcp credit-modeling` and the two environment variables; the README documents this.

### 4.7 Core additions (SP1 hand-off recommendations)

- `modelforge_core.testing.make_synthetic_credit_frame` (moved from `packages/core/tests/synthetic.py`; the tests import it from there).
- `modelforge_core.datasets.synthetic.SyntheticCreditAdapter` registered as `synthetic_credit`: 4,000 rows, six features, vintages 1 to 5, `oot_capable=True`, seeded, no download. Used by the integration test, CI and offline demos.
- `calibration.calibrate_split(frame: pd.DataFrame, manifest: SplitManifest, predict: Callable[[pd.DataFrame], np.ndarray], *, split: Split, method) -> CalibrationMap`: applies the split, predicts, and fits, so the `calibrate` tool does not re-derive the slicing.
- `thresholds.evaluate_thresholds` accepts `benchmark=None` and `sensitivity=None`; the dependent tests become `not_applicable`.
- `predict.predict_model(model: Scorecard | GBMModel, frame) -> np.ndarray` and `predict.parse_model(text: str) -> Scorecard | GBMModel`.
- `Scorecard.fit_method: Literal["single", "stepwise_wrong_sign"] = "single"`; `fit_woe_logistic_stepwise` sets it.

No other core behaviour changes. Core tests keep passing unchanged except for the import path of the synthetic generator.

## 5. Orchestrator

### 5.1 RunState

```python
class RunState(StrictModel):
    run_id: str
    requester: str
    profile: str = "default"
    seed: int
    brief_hash: str
    brief_ref: str                              # artifact id
    dataset_ref: DatasetRef
    replay_of: str | None = None
    plan: ModelingPlan | None = None
    refs: Annotated[dict[RefName, ArtifactRef], merge_refs] = {}
    gates: Annotated[dict[GateName, GateDecision], merge_gates] = {}
    counters: Counters = Counters()             # build_loops, remediation_rounds
    cost_usd: float = 0.0
    errors: Annotated[list[ErrorRecord], add] = []
    status: RunStatus = "running"
```

`RefName` is a Literal of: `dataset, dictionary, profile, leakage, psi, split, binning, selection, champion, challenger_model, calibration, champion_scores, challenger_scores, discrimination, calibration_tests, stability, sensitivity, benchmark, validation, document_sections, document_md, document_html, completeness, findings, responses, manifest, replay_report`. The state holds references only; nodes load artifacts through the store when they need values. The scores for train, validation and out-of-time live in one Parquet artifact with a `split` column (SP1's `SCORES_COLUMNS`), so `champion_scores` and `challenger_scores` replace the umbrella's `scores{train,val,oot}`.

The graph is `StateGraph(RunState, context_schema=Deps)` where `Deps{tools: ToolClient, lineage: Lineage, store: ArtifactStore, settings: Settings}` is passed at invoke time and never checkpointed. `Lineage` is the façade over the lineage repositories (runs, queue, steps, events, artifact rows, approvals, inventory) bound to one `Database`.

### 5.2 Graph

```
START -> intake -> scope -> gate_scope
gate_scope: approve | edit -> profile ; reject -> seal
profile -> split -> features -> build -> validate -> gate_model
gate_model: accept -> document ; adjust(features) -> features ; adjust(build) -> build ; reject -> seal
document -> challenge -> remediate -> gate_signoff -> seal -> END
```

Gate nodes return `Command(goto=...)`. Node contents, following `examples/core_pipeline.py` exactly:

| Node | Kind | Calls |
|---|---|---|
| `intake` | deterministic | parse the brief artifact into `Brief` again (the CLI validated it to fail fast; a schema error here parks the run), create the inventory stub, record `brief_hash` |
| `scope` | stub | `Brief -> ModelingPlan` deterministically: fields copied, thresholds merged over `DEFAULT_THRESHOLDS`; stores `modeling_plan`; sets `runs.plan_ref` |
| `gate_scope` | human | section 5.4 |
| `profile` | tool | `load_dataset`; `profile_dataset`; `check_leakage`; `compute_psi` with `base_vintages` = all vintages not in `plan.vintage_policy.oot_vintages` and `compare_vintages` = the OOT vintages, skipped (no `psi` ref) when the plan has no OOT vintages or the dataset is not OOT-capable |
| `split` | tool | `make_splits` with `strategy="vintage"` when the dataset is OOT-capable and the plan lists OOT vintages, else `stratified_random`; `seed` from state |
| `features` | tool | candidates = dictionary features minus leakage-flagged minus `plan.feature_policy.exclusions`; `bin_features_woe` on train; `select_features` with the plan's IV, correlation and `max_features` |
| `build` | tool | `fit_logistic_pd` (stepwise; `model_name = plan.model_name`) producing the champion and the merged selection; `fit_gbm_challenger` on the champion's surviving features with `model_name = f"{plan.model_name}_gbm"`; `calibrate` on val with Platt, as in the example (`ModelingPlan` has no calibration field; adding one is a later decision); `score` champion with calibration; `score` challenger without. Champion features come from the `fit_logistic_pd` summary, so the node loads no artifact |
| `validate` | tool | `eval_split = "oot" if oot_available else "val"`; `discrimination_metrics`; `calibration_tests(split=eval_split)`; `stability_tests(compare_split=eval_split, features=champion features)`; `sensitivity_analysis`; `benchmark_models(gap_threshold=plan.thresholds.benchmark_gap)`; `evaluate_thresholds(thresholds=plan.thresholds)` |
| `gate_model` | human | section 5.4 |
| `document` | stub + tool | builds `DocumentSections` from fixture Markdown per section with values substituted from artifact summaries and `[A:<id>#<path>]` markers pointing at the run's real artifacts; `render_document`; `check_completeness` |
| `challenge` | stub | fixture `FindingsList` (round 1): the dataset-proxy limitation always; a calibration finding when `cal.binomial` is not `pass`; stored as `findings` artifact and `findings` rows |
| `remediate` | stub | fixture `ResponsesList` accepting every finding with a standard action; stored as `responses`; updates `findings.status/response`; `counters.remediation_rounds = 1` |
| `gate_signoff` | human | section 5.4 |
| `seal` | deterministic | section 5.6 |

Every tool node is idempotent given its input state: re-running overwrites its refs. Each node runs inside a `Step` (section 5.5).

### 5.3 Tool client

```python
class ToolClient(Protocol):
    def call(self, server: ServerName, tool: str, request: StrictModel, *, timeout_s: float | None = None) -> ToolCall
```

`ToolCall{server, tool, version, input_hash, output: StrictModel, refs: list[ArtifactRef], duration_ms}`. `input_hash` is the SHA-256 of the canonical JSON of the request; `version` is the tool's `meta.version` read from `list_tools` at connect time; `refs` are the `ArtifactRef` fields of the validated output model (validated against `TOOLS[(server, tool)].output_model`).

`StdioToolClient(settings)` is a context manager. On enter it starts one background thread running an asyncio loop, opens an `mcp.Client(StdioServerParameters(command, args, env))` per server (default command `[sys.executable, "-m", "modelforge_mcp", <server>, "--marker", <uuid>]` with the artifact, data and templates directories in `env` and nothing else from the parent environment beyond `PATH`-style basics; overridable through `MODELFORGE_MCP_COMMAND`, which must accept the trailing `<server> --marker <uuid>` arguments because the client tracks server processes by that marker), and lists tools. Checkpoint state is serialised with an explicit msgpack allowlist of the four schema modules' Pydantic models. `call` schedules `client.call_tool(name, {"request": request.model_dump(mode="json")}, read_timeout_seconds=timeout_s)` on that loop and blocks on the future. On exit it closes the sessions and the subprocesses. One client lives for the duration of one run execution, so the four subprocesses start once per claim, not once per node.

`InProcessToolClient(servers)` uses the same bridge over `mcp.Client(server_instance)`; it is a real MCP round trip without subprocesses and is what unit tests of nodes use.

The MCP SDK is async-only; the graph, lineage and CLI are synchronous. This bridge is the one place the two meet.

Retries: `call` retries only `transport` and `timeout` errors, three attempts with 1, 2 and 4 second backoff; a deterministic tool returns the same `invalid_input` twice, so those are not retried. The final failure raises `ToolError` out of the node.

### 5.4 Gates

A gate node builds `GatePrompt{gate, run_id, summary}` (the summary holds the refs and headline values a reviewer needs: the plan at `scope`, the validation statuses at `model`, the completeness and findings counts at `signoff`), appends `gate.requested`, and calls `decision = interrupt(prompt.model_dump(mode="json"))`. The returned value is validated into `GateDecision` and applied:

| Gate | Decisions | Effect |
|---|---|---|
| `scope` | `approve`, `edit` (with `plan`), `reject` | `edit` replaces `state.plan` and stores a new `modeling_plan` artifact; `reject` -> `seal` |
| `model` | `accept`, `adjust` (with `plan` and `adjust_target: features | build`), `reject` | `adjust` replaces the plan, increments `counters.build_loops` (bound 3, enforced at approval time), and routes to the target node; `reject` -> `seal` |
| `signoff` | `approve`, `approve_with_conditions` (with `conditions`), `reject` | recorded on the inventory entry at `seal` |

`GateDecision(StrictModel)`: `gate, decision, approver, comment="", plan: ModelingPlan | None = None, adjust_target: Literal["features", "build"] | None = None, conditions: list[str] = []`. The CLI `approve` command records it (section 3.6), enqueues `resume`, and the worker resumes with `Command(resume=decision.model_dump(mode="json"))` on the run's thread.

In replay mode (`state.replay_of` set) a gate node applies the original run's approvals for that gate in `ts` order without interrupting: the n-th visit to the gate takes the n-th approval, so an original `adjust` loop is replayed as it happened.

### 5.5 Steps, lineage writes and error handling

`Step(deps, state, node)` is a context manager used by every node. On enter it inserts the `steps` row (attempt = 1 + previous attempts of that node for the run) and appends `step.started` with the input state hash (SHA-256 of the canonical JSON of the state). `step.tool(server, tool, request)` calls the tool client and, in one transaction, inserts the `tool_calls` row, inserts an `artifacts` row per ref (`producer_step_id` = this step), and appends `tool.called` and `artifact.stored` events. `step.store(model_or_bytes, kind)` does the same for artifacts the node writes itself (plan, sections, findings, manifest). On normal exit it writes `output_state_hash`, status `succeeded`, and `step.finished`. On exception it marks the step `failed`, appends `error.recorded` with `ErrorRecord{node, attempt, kind: "tool" | "validation" | "internal", error, ts}`, and re-raises.

The worker catches the exception, writes `runs.error`, sets status `needs_attention`, and calls `graph.update_state(config, {"errors": [record]})`. `modelforge resume <run_id>` clears `runs.error` and enqueues `resume`; the checkpointer restarts from the failed node (`attempt` increments). `modelforge abort <run_id>` sets `aborted`.

Cost accounting and the cost cap are carried as columns and state fields but not enforced in SP2 (no LLM calls); the cap check lands with the gateway in SP3. Timeouts are per tool call (`MODELFORGE_TOOL_TIMEOUT_S`, default 600).

### 5.6 Seal and manifest

```python
class Manifest(StrictModel):
    run_id: str; replay_of: str | None
    brief_hash: str; dataset_ref: DatasetRef; dataset_id: str | None; seed: int; profile: str   # dataset_id is None when the run was rejected before the dataset loaded
    initial_plan_ref: str                       # the scope node's plan, before any gate edit
    plan_ref: str                               # the final plan
    refs: dict[RefName, ArtifactRef]
    tool_versions: dict[str, str]               # "server/tool" -> version, from tool_calls
    approvals: list[ApprovalSummary]            # gate, decision, approver (no timestamp: approval times live in the approvals table)
    outcome: Literal["completed", "rejected"]
    core_version: str; orchestrator_version: str
    events_head_hash: str                       # hash of the run's last event before the manifest is stored
```

`seal` stores the manifest (kind `manifest`), sets `runs.manifest_hash` to its id, appends `run.sealed`, finalises the inventory entry (status from the sign-off decision, `champion_ref`, `validation_summary` = overall plus per-test statuses, approver, conditions), and sets the run status to `completed` or `rejected`. The manifest contains no timestamp (the seal event does).

### 5.7 Replay

`modelforge replay <run_id>` requires a sealed original (`completed` or `rejected`). It creates a new run with `replay_of = run_id`, the same `brief_ref`, `brief_hash`, `dataset_ref`, `seed` and `profile`, requester `replay`, and enqueues kind `replay`. During execution: `scope` loads the original's initial plan (`Manifest.initial_plan_ref`) into `state.plan` instead of regenerating it; `document` copies the original `document_sections` ref and re-runs `render_document` and `check_completeness`, so the rendered document is itself a compared artifact; `challenge` and `remediate` copy the original `findings` and `responses` refs and re-insert the `findings` rows from them (in SP3 `--rerun-llm` re-executes all four); gates apply the original approvals in order without interrupting (section 5.4); every tool node re-executes. At `seal` the replay compares its refs with the original manifest's over the deterministic keys (`dataset, dictionary, profile, leakage, psi, split, binning, selection, champion, challenger_model, calibration, champion_scores, challenger_scores, discrimination, calibration_tests, stability, sensitivity, benchmark, validation, document_md, document_html, completeness`) and stores `ReplayReport{original_run_id, matched: list[RefName], mismatched: list[{ref, original_id, replay_id}], missing: list[RefName], ok: bool}`, appends `replay.completed` on both runs, and the CLI prints the report and exits 1 if `ok` is false.

### 5.8 Worker

`modelforge worker [--once] [--poll-interval 2.0]`: applies migrations, then loops: `claim`; if none, sleep and retry (exit when `--once`); otherwise `execute(run_id, kind)`:

1. Set status `running`, append `run.claimed`.
2. Open `StdioToolClient` and the `PostgresSaver`; compile the graph; `config = {"configurable": {"thread_id": run_id}}`.
3. `kind == "start" | "replay"`: `graph.invoke(initial_state, config, context=deps)`. `kind == "resume"` after a gate (the run was `awaiting_<gate>`): load the latest approval row for that gate and `graph.invoke(Command(resume=approval.payload), config, context=deps)`. `kind == "resume"` after `needs_attention`: `graph.invoke(None, config, context=deps)`, which continues from the last checkpoint and re-runs the failed node. The run's status before the claim tells the two apart.
4. If the result carries `__interrupt__`, set status `awaiting_<gate>` from the interrupt value. Otherwise the state's status is terminal (`completed` or `rejected`).
5. On exception: section 5.5.
6. `release` the queue row.

A claim whose kind does not fit the run's status (a `resume` for a sealed run, a `start` for a run that is not `queued`, a gate resume with no recorded approval) is rejected before any status write: the worker appends `worker.rejected_claim`, releases the queue row and leaves the run untouched.

`worker_id` is `hostname:pid`. The same `execute` function serves the foreground CLI.

### 5.9 CLI

Typer application `modelforge` (entry point in `modelforge_orchestrator.cli`):

| Command | Behaviour |
|---|---|
| `migrate` | apply migrations and checkpointer setup |
| `run <brief.yaml> [--requester NAME] [--profile default] [--detach]` | validate the brief, store it as an artifact, create the run (`queued`), enqueue `start`; without `--detach`, execute it in the foreground until it parks or ends, then print the status and the gate prompt summary |
| `approve <run_id> <gate> --decision D --approver NAME [--comment] [--plan plan.yaml] [--adjust-target features|build] [--condition TEXT]... [--detach]` | record the approval (four-eyes and loop bounds enforced), enqueue `resume`; foreground execution as above |
| `status <run_id>` | status, awaiting gate, step list, refs with summaries, error if parked |
| `resume <run_id> [--detach]`, `abort <run_id>` | section 5.5 |
| `worker [--once] [--poll-interval S]` | section 5.8 |
| `replay <run_id> [--detach]` | section 5.7; exit 1 on mismatch |
| `verify <run_id>` | section 3.8; exit 1 on failure |

Exit codes: 0 success, 1 verification or replay mismatch, 2 usage or validation error (including four-eyes and gate mismatch). Output is plain text, one fact per line; `--json` on `status`, `replay` and `verify` prints the report model.

### 5.10 Configuration

`Settings` (a frozen dataclass built from the environment, with a `.env` file honoured when present): `database_url` (`MODELFORGE_DATABASE_URL`, default `postgresql://modelforge:modelforge@localhost:5432/modelforge`), `artifact_dir` (`MODELFORGE_ARTIFACT_DIR`, default `./artifacts`), `data_dir` (`MODELFORGE_DATA_DIR`, default `./data`), `templates_dir` (`MODELFORGE_TEMPLATES_DIR`, default `./templates`), `profile` (`MODELFORGE_PROFILE`, default `default`), `mcp_command` (`MODELFORGE_MCP_COMMAND`, optional), `tool_timeout_s` (`MODELFORGE_TOOL_TIMEOUT_S`, default 600), `log_level` (`MODELFORGE_LOG_LEVEL`, default `INFO`). The profile is recorded on the run and otherwise unused until SP3.

### 5.11 Brief

`Brief(StrictModel)` in `modelforge_orchestrator.briefs`, loaded from YAML:

```yaml
model_name: polish_bankruptcy_pd
portfolio: Polish non-financial companies (UCI bankruptcy data)
dataset: {adapter: polish_bankruptcy, seed: 42}
target: {event: "bankruptcy within the forecasting horizon, as a proxy for default", horizon: "1 to 5 years by horizon file"}
intended_use: reference PD model for a wholesale credit portfolio; demonstration only
regulatory_scope: [SR 11-7, SR 26-2]
tier: 2
vintage_policy: {oot_vintages: [5]}
feature_policy: {exclusions: [], max_features: 20, iv_min: 0.02, iv_max: 0.5, corr_max: 0.7}
thresholds: {}            # overrides of Thresholds fields; defaults otherwise
constraints: "No raw rows leave the toolkit; document the horizon-files-as-vintages proxy."
```

`examples/briefs/polish-pd.yaml` and `examples/briefs/synthetic.yaml` (adapter `synthetic_credit`) ship with SP2. The brief's SHA-256 is `brief_hash`; the YAML bytes are the `brief` artifact.

## 6. Compose, Dockerfile, CI

`Dockerfile`: `python:3.12-slim`, `libgomp1` for LightGBM, uv copied from the pinned `ghcr.io/astral-sh/uv` image, `uv sync --frozen --no-dev`, entrypoint `uv run modelforge`. `docker-compose.yml`: `postgres` (`pgvector/pgvector:pg16`, user, password and database `modelforge`, `pg_isready` healthcheck, named volume, port 5432) and `worker` (built from the Dockerfile, `depends_on: postgres: condition: service_healthy`, `MODELFORGE_DATABASE_URL` pointing at the service, `./artifacts` and `./data` bind-mounted, command `worker`). `api` and `jaeger` arrive in SP5.

Makefile additions: `db-up` (`docker compose up -d postgres`), `db-down`, `up` (`docker compose up -d --build`), `down`, `migrate`, `worker`, `goldens`.

`ci.yml`: the `lint-test` job gains a `services.postgres` container (`pgvector/pgvector:pg16`, health options, port 5432) and the environment `MODELFORGE_DATABASE_URL=postgresql://modelforge:modelforge@localhost:5432/modelforge`, `MODELFORGE_REQUIRE_DB=1`; a second job `docker-build` builds the image. The dataset cache step stays.

## 7. Testing

Test layout: `packages/<pkg>/tests/`; root `testpaths` lists all four. Marker `db` for tests needing Postgres; marker `slow` as before.

Database fixture: `modelforge_lineage.testing.temporary_database(admin_url)` creates `modelforge_test_<hex>`, applies migrations and the checkpointer setup, yields its URL, and drops it at session end; a function-scoped fixture truncates every table between tests. When Postgres is unreachable the `db` tests skip with the reason printed, unless `MODELFORGE_REQUIRE_DB=1` (CI), in which case they fail.

| Package | Tests |
|---|---|
| lineage | migrations apply twice idempotently; event chain append, verify, tamper detection (payload edit and deletion); two concurrent claimers; approvals: gate mismatch, four-eyes, loop bound, plan diff; store: put/get, idempotent re-put, atomic write (no file under a wrong id after a simulated failure), Parquet byte determinism; inventory versioning; `verify_run` on a tampered artifact |
| mcp | one test module per server using `InProcessToolClient` semantics (`mcp.Client(server)`): every tool on the synthetic dataset produces a ref whose artifact parses as the declared core model; error paths (`artifact_not_found`, `invalid_input`); doc-renderer rendering, banner for a missing section, completeness report; contract snapshots: `packages/mcp/contracts/<server>/<tool>@<version>.json` holding input and output JSON schema (platform-independent, committed) and `packages/mcp/contracts/goldens/<platform>/<server>/<tool>@<version>.sha256` (platform key `f"{sys.platform}-{platform.machine()}"`, for example `darwin-arm64`, `linux-x86_64`) holding the output artifact hash on the synthetic dataset; a schema or hash change without a version bump fails; `make goldens` regenerates; a platform with no committed goldens skips the hash comparison with a visible reason, and CI uploads its freshly computed goldens as a workflow artifact so they can be committed |
| orchestrator | brief parsing; scope stub golden plan; each tool node against `InProcessToolClient` and the temporary database (refs set, steps and tool_calls rows written, events chained); gate nodes with `Command(resume=...)`; worker interrupt handling and `needs_attention` parking on an injected tool failure, then `resume`; the full-graph integration test on `examples/briefs/synthetic.yaml` through the CLI functions with real stdio servers and all three gates, then `replay` (asserting `ok` and byte-identical artifacts) and `verify`; four-eyes rejection through the CLI; a `slow` test running `examples/briefs/polish-pd.yaml` end to end and asserting the OOT AUC and the sealed manifest |

The no-pickle scan covers all four packages. Import-linter contracts are part of `make lint`.

## 8. Deferred and out of scope

- PDF rendering (SP5, with the console).
- Cost cap enforcement, prompt hashes in the manifest, `llm_calls` rows (SP3).
- Real `scope`, `document`, `challenge`, `remediate` behaviour (SP3, SP4).
- Tracing and the Jaeger service (SP5). Structured logging only.
- API, SSE, identity header (SP5). CLI identity is an argument.
- S3 artifact store (documented as the production path, not implemented).

## 9. Risks

- **Cross-platform golden hashes.** Fitted-model outputs (LightGBM text, logistic coefficients) may differ in the last digits between macOS arm64 and Linux x86_64. Goldens are therefore per platform; replay equality within one environment is the reproducibility guarantee the umbrella spec makes.
- **Parquet determinism** depends on a fixed pyarrow version; `uv.lock` pins it and a test asserts byte equality.
- **LangGraph interrupt semantics** (`__interrupt__` in the invoke result; resuming a `needs_attention` run) are verified in the first orchestrator task before the worker is built.
- **Subprocess lifecycle.** Four stdio servers per run execution; the client must terminate them on every exit path, including exceptions and `needs_attention`.

## 10. As-built amendments (2026-11-09)

Decisions made during execution that change or sharpen the text above; each is recorded with its reasoning in `docs/superpowers/notes/2026-11-09-sp2-handoff.md`.

- The LangGraph checkpointer tables are created by the orchestrator (`modelforge migrate`, worker start), not by `modelforge_lineage.migrate`, which may not import LangGraph (3.1).
- `tool_calls.seq` (migration `0002`) gives tool calls an insertion order (3.2). Findings rows are written through `modelforge_lineage.findings`; the row id is `<run_id>/<finding id>` because the table's primary key is `id` alone.
- Event timestamps are rebuilt on read as UTC ISO-8601 microseconds so the hashed body survives the `timestamptz` round trip (3.3).
- `ApprovalSummary` carries no timestamp, honouring the no-wall-clock rule for the manifest; `Manifest.dataset_id` is nullable; `Manifest.initial_plan_ref` is the earliest `modeling_plan` artifact of the run (5.6).
- `profile_dataset` and `check_leakage` take an optional `dataset_name`; `stability_tests` takes the plan's `thresholds` (4.2, 4.4).
- Citation path grammar for list-valued artifact fields; raw HTML disabled in the renderer (4.5).
- Servers are launched with `--marker <uuid>` and tracked by it; the stdio client passes a minimal environment; checkpoint state uses an explicit msgpack allowlist (5.3).
- The worker validates a claim against the run's status before writing `running` and rejects mismatches with `worker.rejected_claim` (5.8). `abort` refuses `running` runs (no cancellation in SP2) and releases the queue row of a `queued` run.
- `run` validates the brief fully, including the threshold overrides, before creating a run (5.9). Plain output shows the gate prompt summary; `status --json` includes `gate_prompt`.
- `verify` also anchors the chain on the manifest's `events_head_hash` and the run's brief artifact has an `artifacts` row (3.8).
- Shared Postgres test fixtures are imported into each package's `conftest.py` (pytest rejects `pytest_plugins` below the rootdir); CLI tests drive the installed entry point as a subprocess because the MCP stdio transport needs a real stderr file descriptor (7).
