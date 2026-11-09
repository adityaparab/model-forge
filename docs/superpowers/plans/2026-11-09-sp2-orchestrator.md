# SP2: Lineage, MCP Servers, Orchestrator, Worker and CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the governed run around the SP1 toolkit: Postgres lineage with a hash-chained event log and a content-addressed artifact store, four MCP servers over `modelforge_core`, a LangGraph orchestrator whose deterministic nodes call the servers over stdio and whose three human gates are resumable interrupts, a queue worker, and the `modelforge` CLI with `run`, `approve`, `replay` and `verify`.

**Architecture:** Servers are database-free functions over the artifact store that return `ArtifactRef`s; the orchestrator's MCP client records every call in lineage inside the step that made it. The graph, lineage and CLI are synchronous; one background event-loop thread per run execution bridges to the async MCP client. Replay is a new run that re-executes every tool node and compares its refs with the sealed manifest.

**Tech Stack:** Python 3.12 (uv workspace), psycopg 3 with `psycopg_pool`, plain SQL migrations, pyarrow Parquet, structlog, `mcp` 2.3 (`MCPServer`, `Client`, `StdioServerParameters`), LangGraph 1.2 with `langgraph-checkpoint-postgres`, Typer, Jinja2, markdown-it-py, PyYAML, pytest, Docker Compose (`pgvector/pgvector:pg16`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-09-sp2-orchestrator-design.md` (SP2 spec); `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` sections 4.2, 5, 6, 10, 11 (umbrella).

## Global Constraints

- Python `>=3.12,<3.13`; one uv workspace, members `packages/*`.
- Layering `core <- lineage <- mcp <- orchestrator`, enforced by import-linter. `modelforge_lineage` may not import `mcp`, `langgraph`, `langchain*`, `modelforge_mcp`, `modelforge_orchestrator`. `modelforge_mcp` may not import `langgraph`, `langchain*`, `psycopg`, `modelforge_orchestrator`. No package imports `anthropic`, `openai`, `ollama`, `langchain_anthropic`, `langchain_openai`, `langchain_ollama`, `httpx` or `requests`; the single exception stays `modelforge_core.datasets.download -> httpx`. `modelforge_core` keeps its SP1 forbidden list plus `psycopg`, `structlog`, `typer`.
- No `pickle`, `joblib.dump`, `cloudpickle` or `dill` in any `modelforge_*` package; the scan test covers all four packages.
- No wall-clock value inside any artifact. Timestamps live in lineage rows only.
- Every function that uses randomness takes an explicit `seed: int`.
- MCP servers write nothing to stdout except protocol frames; logs are structlog JSON on stderr.
- Tools accept artifact ids, `DatasetRef`s and template ids, never filesystem paths.
- Tool versions start at `1.0.0`; an algorithm or schema change bumps the version; contract snapshots are committed.
- Identifiers: `run_`, `step_`, `tc_`, `apr_` followed by 12 hex characters of `uuid4().hex`; artifact ids are the SHA-256 hex digest of the bytes.
- Default Postgres URL `postgresql://modelforge:modelforge@localhost:5432/modelforge`; Compose user, password and database are all `modelforge`.
- Tests that need Postgres carry `@pytest.mark.db`; they skip with a printed reason when the database is unreachable unless `MODELFORGE_REQUIRE_DB=1` (set in CI), in which case they fail.
- The repository stays employer-neutral: no company names in committed files.
- Commit after every task, conventional-commit style, ending with the `Co-Authored-By` trailer of the model that authored the commit.

## Review Focus

1. **A tool subprocess that dies mid-call** (killed, or failing at import). The client surfaces `ToolError(code="transport")`, the step is marked failed, the run parks in `needs_attention`, and no server process is left behind. Tests in Task 13 (`test_dead_server_is_transport_error_and_no_orphans`) and Task 18 (`test_tool_failure_parks_run_and_resume_continues`).
2. **An approval for a gate the run is not waiting at** (a second approval, or approving a `completed` run). `record_approval` raises `GateMismatchError` and writes nothing. Tests in Task 6 (`test_record_approval_rejects_wrong_gate`) and Task 19 (`test_cli_approve_wrong_gate_exits_2`).
3. **A brief naming an unknown dataset adapter, or an out-of-time vintage absent from the data.** `run` rejects the unknown adapter before creating a run (exit 2); the missing vintage surfaces as `invalid_input` from `make_splits`, parks the run, and `status` shows the message. Tests in Task 7 (`test_make_splits_missing_vintage_is_invalid_input`) and Task 19 (`test_cli_run_unknown_adapter_exits_2`).
4. **Two workers polling the same queue.** A claim held open in one transaction never blocks or duplicates the other worker's claim. Test in Task 5 (`test_concurrent_claims_take_different_runs`).
5. **A tampered artifact file or event row after sealing.** `verify` names the bad artifact or event and exits 1. Tests in Task 6 (`test_verify_run_reports_tampered_artifact_and_event`) and Task 19 (`test_cli_verify_detects_tamper`).

---

## File structure

```
pyproject.toml                                  workspace: four members, dev deps, pytest (importlib mode, markers), pyright, import-linter (Task 1)
Makefile                                        + db-up, db-down, migrate, worker, goldens, up, down (Tasks 1, 11, 19, 20)
docker-compose.yml                              postgres (Task 1); worker (Task 20)
Dockerfile                                      (Task 20)
.github/workflows/ci.yml                        Postgres service, REQUIRE_DB, docker-build job (Task 20)
templates/sr11-7-dev-doc/sections.yaml          eleven sections (Task 10)
templates/sr11-7-dev-doc/document.md.j2         Markdown template (Task 10)
examples/briefs/polish-pd.yaml, synthetic.yaml  (Task 12)

packages/core/modelforge_core/
  testing.py                                    make_synthetic_credit_frame, moved from tests (Task 1)
  datasets/synthetic.py                         SyntheticCreditAdapter (Task 2)
  datasets/registry.py                          seed-aware factories, synthetic_credit (Task 2)
  calibration.py                                + calibrate_split (Task 2)
  thresholds.py                                 optional sensitivity and benchmark (Task 2)
  predict.py                                    parse_model, predict_model (Task 2)
  schemas.py, logistic.py                       Scorecard.fit_method (Task 2)

packages/lineage/modelforge_lineage/
  ids.py            new_id(prefix)                                    (Task 3)
  canonical.py      canonical_json, sha256_hex                        (Task 3)
  models.py         ArtifactRef, Producer, records, GateDecision, reports, Literals (Task 3)
  db.py             Database (pool, connection(), transaction())      (Task 3)
  migrate.py        apply, TABLES                                     (Task 3)
  migrations/0001_initial.sql                                         (Task 3)
  testing.py        temporary_database + pytest fixtures              (Task 3)
  observability.py  configure_logging, get_logger                     (Task 3)
  store.py          ArtifactStore                                     (Task 4)
  events.py         append_event, list_events, verify_chain           (Task 5)
  runs.py           create_run, get_run, set_status, setters          (Task 5)
  queue.py          enqueue, claim, release                           (Task 5)
  steps.py          start_step, finish_step, tool calls, artifact rows (Task 5)
  approvals.py      record_approval, list_approvals, plan_diff        (Task 6)
  inventory.py      create_stub, finalise, get_for_run                (Task 6)
  verify.py         verify_run                                        (Task 6)
  facade.py         Lineage                                           (Task 6)

packages/mcp/modelforge_mcp/
  common.py         ServerSettings, ToolErrorPayload, fail, tool registration helper, loaders (Task 7)
  schemas.py        DatasetRef, document models                       (Tasks 7, 10)
  registry.py       ToolSpec, TOOLS, SERVERS, build_server            (Task 7; extended 8-10)
  servers/data_profiler.py, credit_modeling.py, validation.py, doc_renderer.py  (Tasks 7-10)
  __main__.py       modelforge-mcp <server> | contracts | goldens     (Tasks 7, 11)
  contracts.py      schema snapshots, golden chain                    (Task 11)
packages/mcp/contracts/<server>/<tool>@<version>.json                 (Task 11)
packages/mcp/contracts/goldens/<platform>/<server>/<tool>@<version>.sha256 (Task 11)

packages/orchestrator/modelforge_orchestrator/
  config.py         Settings, load_settings                           (Task 12)
  briefs.py         Brief, load_brief, brief_hash, plan_from_brief    (Task 12)
  schemas.py        RunState, RefName, Counters, ErrorRecord, GatePrompt, Manifest, ReplayReport, findings models (Task 12)
  tools.py          ToolError, ToolCall, ToolClient, _Bridge, StdioToolClient, InProcessToolClient (Task 13)
  deps.py           Deps                                              (Task 13)
  steps.py          Step, state_hash                                  (Task 14)
  nodes/intake.py, scope.py, profile.py, split.py                     (Task 14)
  nodes/features.py, build.py, validate.py                            (Task 15)
  nodes/gates.py    gate_scope, gate_model, gate_signoff              (Task 16)
  graph.py          build_graph                                       (Task 16)
  nodes/document.py, challenge.py, remediate.py, seal.py              (Task 17)
  fixtures/sections.yaml, findings.yaml                               (Task 17)
  replay.py         create_replay, compare_refs                       (Task 17, 18)
  worker.py         execute, run_loop, run_foreground                 (Task 18)
  cli.py            Typer app                                         (Task 19)
packages/<pkg>/tests/                            one test module per source module; `db` marker where Postgres is used
```

Test directories have no `__init__.py`; pytest runs with `--import-mode=importlib`, so same-named test files in different packages do not collide.

---

### Task 1: Workspace skeleton, Compose Postgres, synthetic generator move

**Files:**
- Create: `packages/lineage/pyproject.toml`, `packages/lineage/modelforge_lineage/__init__.py`, `packages/lineage/tests/test_version.py`
- Create: `packages/mcp/pyproject.toml`, `packages/mcp/modelforge_mcp/__init__.py`, `packages/mcp/tests/test_version.py`
- Create: `packages/orchestrator/pyproject.toml`, `packages/orchestrator/modelforge_orchestrator/__init__.py`, `packages/orchestrator/tests/test_version.py`
- Create: `packages/core/modelforge_core/testing.py`, `docker-compose.yml`
- Modify: `pyproject.toml`, `Makefile`, `packages/core/tests/conftest.py`, `packages/core/tests/test_binning.py`, `packages/core/tests/test_no_pickle.py`
- Delete: `packages/core/tests/synthetic.py`

**Interfaces:**
- Produces: packages `modelforge-lineage`, `modelforge-mcp`, `modelforge-orchestrator` (hatchling, `version = "0.1.0"`, `__version__ = "0.1.0"`), dependencies per spec section 2 (`modelforge-lineage`: `modelforge-core`, `psycopg[binary]>=3.3`, `psycopg-pool>=3.3`, `structlog>=26`, `pydantic>=2.7`, `pyarrow>=16`; `modelforge-mcp`: `modelforge-core`, `modelforge-lineage`, `mcp>=2.3`, `jinja2>=3.1`, `markdown-it-py>=3`, `pyyaml>=6`; `modelforge-orchestrator`: `modelforge-core`, `modelforge-lineage`, `modelforge-mcp`, `langgraph>=1.2`, `langgraph-checkpoint-postgres>=3.2`, `typer>=0.28`, `pyyaml>=6`). Console scripts: `modelforge = "modelforge_orchestrator.cli:app"` and `modelforge-mcp = "modelforge_mcp.__main__:main"` (modules arrive in Tasks 19 and 7).
- Produces: `modelforge_core.testing.make_synthetic_credit_frame` (the SP1 generator, moved verbatim).
- Produces: `make db-up` / `make db-down`; Postgres reachable at the default URL.

- [ ] **Step 1: Write the three package `pyproject.toml` files and `__init__.py` with `__version__`, plus a `test_version.py` per package asserting `"0.1.0"`**

- [ ] **Step 2: Edit the root `pyproject.toml`**

`[project] dependencies` lists the four packages; `[tool.uv.sources]` marks each `workspace = true`. Pytest: `testpaths = ["packages/core/tests", "packages/lineage/tests", "packages/mcp/tests", "packages/orchestrator/tests"]`, `addopts = "-ra --import-mode=importlib"`, markers `slow` (existing text) and `db: needs Postgres; skips when unreachable unless MODELFORGE_REQUIRE_DB=1`. Import-linter: `root_packages = ["modelforge_core", "modelforge_lineage", "modelforge_mcp", "modelforge_orchestrator"]`, `include_external_packages = true`, contracts: (a) the existing core contract with `psycopg`, `structlog`, `typer` appended; (b) `type = "layers"`, `layers = ["modelforge_orchestrator", "modelforge_mcp", "modelforge_lineage", "modelforge_core"]`; (c) `forbidden` from `modelforge_lineage` to `mcp`, `langgraph`, `langchain`, `langchain_core`, `modelforge_mcp`, `modelforge_orchestrator`; (d) `forbidden` from `modelforge_mcp` to `langgraph`, `langchain`, `langchain_core`, `psycopg`, `modelforge_orchestrator`; (e) `forbidden` from all four to `anthropic`, `openai`, `ollama`, `langchain_anthropic`, `langchain_openai`, `langchain_ollama`, `httpx`, `requests` with `ignore_imports = ["modelforge_core.datasets.download -> httpx"]`.

- [ ] **Step 3: Move `packages/core/tests/synthetic.py` to `packages/core/modelforge_core/testing.py` and point `conftest.py` and `test_binning.py` at `from modelforge_core.testing import make_synthetic_credit_frame`; remove `__all__` re-export from conftest**

- [ ] **Step 4: Widen `test_no_pickle.py` to scan every `packages/*/modelforge_*` directory**

```python
def test_no_pickle_in_any_package() -> None:
    packages = Path(__file__).resolve().parents[3] / "packages"
    roots = sorted(p for p in packages.glob("*/modelforge_*") if p.is_dir())
    assert len(roots) >= 4
    offenders = [p for root in roots for p in root.rglob("*.py") if FORBIDDEN.search(p.read_text())]
    assert offenders == []
```

- [ ] **Step 5: Write `docker-compose.yml` with the `postgres` service and the Makefile targets**

Service `postgres`: `image: pgvector/pgvector:pg16`, `environment: POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB = modelforge`, `ports: ["5432:5432"]`, `volumes: [pgdata:/var/lib/postgresql/data]`, `healthcheck: test: ["CMD-SHELL", "pg_isready -U modelforge -d modelforge"], interval: 5s, timeout: 3s, retries: 20`. Makefile: `db-up: docker compose up -d --wait postgres`, `db-down: docker compose down`.

- [ ] **Step 6: Sync, start Postgres, lint and test**

Run: `uv sync --all-groups && make db-up && make lint && uv run pytest -m "not slow"`
Expected: lint clean including `lint-imports` with five contracts kept; every non-slow core test passes plus three version tests.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: lineage, mcp and orchestrator workspace members; Compose Postgres; synthetic generator in core.testing"
```

---

### Task 2: Core additions from the SP1 hand-off

**Files:**
- Create: `packages/core/modelforge_core/datasets/synthetic.py`, `packages/core/modelforge_core/predict.py`
- Modify: `packages/core/modelforge_core/datasets/registry.py`, `calibration.py`, `thresholds.py`, `schemas.py`, `logistic.py`
- Test: `packages/core/tests/test_synthetic_adapter.py`, `test_predict.py`, `test_registry.py` (modify), `test_calibration.py` (add), `test_thresholds.py` (add), `test_logistic.py` (add)

**Interfaces:**
- Produces:
  - `class SyntheticCreditAdapter: name = "synthetic_credit"; def __init__(self, *, seed: int = 42, n: int = 4000); def load(self, cache_dir: Path) -> LoadedDataset` (frame from `make_synthetic_credit_frame(n, seed=seed)`; dictionary originals `x_1..x_6` with description `"synthetic ratio k"`; `oot_capable=True`; writes nothing under `cache_dir`).
  - `registry.ADAPTERS: dict[str, Callable[[int], DatasetAdapter]]` (factories take `seed`; Polish ignores it); `get_adapter(name: str, *, seed: int = DEFAULT_SEED) -> DatasetAdapter`; `load_dataset(name: str, cache_dir: Path, *, seed: int = DEFAULT_SEED) -> LoadedDataset`.
  - `calibration.calibrate_split(frame: pd.DataFrame, manifest: SplitManifest, predict: Callable[[pd.DataFrame], np.ndarray], *, split: Split, method: Literal["platt", "isotonic", "none"]) -> CalibrationMap` (`fitted_on=split`).
  - `thresholds.evaluate_thresholds(..., sensitivity: SensitivityResults | None, benchmark: BenchmarkResults | None, ...)`: when `sensitivity is None`, `sens.signs` and `sens.monotonic` are `not_applicable` with message `"No sensitivity analysis was supplied."`; when `benchmark is None`, `bench.gap` is `not_applicable` with `"No challenger benchmark was supplied."`.
  - `predict.parse_model(text: str) -> Scorecard | GBMModel` (`ValueError` when neither validates); `predict.predict_model(model: Scorecard | GBMModel, frame: pd.DataFrame) -> np.ndarray` (dispatches to `predict_pd` or `predict_gbm`).
  - `Scorecard.fit_method: Literal["single", "stepwise_wrong_sign"] = "single"`; `fit_woe_logistic_stepwise` returns a scorecard with `fit_method="stepwise_wrong_sign"`.

- [ ] **Step 1: Write the failing tests**

```python
# test_synthetic_adapter.py
def test_synthetic_adapter_is_seeded_and_canonical(tmp_path: Path) -> None:
    a = SyntheticCreditAdapter(seed=3, n=500).load(tmp_path)
    b = SyntheticCreditAdapter(seed=3, n=500).load(tmp_path)
    validate_canonical(a.frame)
    assert a.frame.equals(b.frame) and a.oot_capable and len(a.frame) == 500
    assert a.dictionary.feature_names == [f"f_{i}" for i in range(1, 7)]
    assert not any(tmp_path.iterdir())

# test_registry.py (add)
def test_registry_passes_seed_to_seeded_adapters() -> None:
    assert get_adapter("synthetic_credit", seed=9).seed == 9  # type: ignore[attr-defined]
    assert get_adapter("polish_bankruptcy").name == "polish_bankruptcy"

# test_predict.py
def test_parse_and_predict_dispatch(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    gbm = fit_gbm(synthetic, ["f_1", "f_2"], model_name="g", seed=0)
    assert isinstance(parse_model(to_json(sc)), Scorecard)
    assert isinstance(parse_model(gbm.model_dump_json()), GBMModel)
    np.testing.assert_allclose(predict_model(sc, synthetic), predict_pd(sc, synthetic))
    np.testing.assert_allclose(predict_model(gbm, synthetic), predict_gbm(gbm, synthetic))
    with pytest.raises(ValueError):
        parse_model("{}")

# test_calibration.py (add)
def test_calibrate_split_matches_manual_fit(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    tables = fit_binning(apply_split(synthetic, manifest, "train"), ["f_1", "f_2"])
    sc = fit_woe_logistic(apply_split(synthetic, manifest, "train"), tables, ["f_1", "f_2"], model_name="m")
    val = apply_split(synthetic, manifest, "val")
    manual = fit_calibration(predict_pd(sc, val), val["default_flag"].to_numpy(), method="platt", fitted_on="val")
    assert calibrate_split(synthetic, manifest, lambda f: predict_pd(sc, f), split="val", method="platt") == manual

# test_thresholds.py (add)
def test_missing_sensitivity_and_benchmark_are_not_applicable(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic)
    inputs["sensitivity"] = None; inputs["benchmark"] = None
    report = evaluate_thresholds(**inputs, model_name="m", thresholds=Thresholds(), oot_available=True)
    assert {report.get(t).status for t in ("sens.signs", "sens.monotonic", "bench.gap")} == {"not_applicable"}
    assert "sensitivity" in report.get("sens.signs").message.lower()

# test_logistic.py (add)
def test_stepwise_records_fit_method(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2", "f_3"])
    sc, _ = fit_woe_logistic_stepwise(synthetic, tables, ["f_1", "f_2", "f_3"], model_name="m")
    assert sc.fit_method == "stepwise_wrong_sign"
    assert fit_woe_logistic(synthetic, tables, ["f_1"], model_name="m").fit_method == "single"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_synthetic_adapter.py packages/core/tests/test_predict.py packages/core/tests/test_registry.py packages/core/tests/test_calibration.py packages/core/tests/test_thresholds.py packages/core/tests/test_logistic.py -v`
Expected: FAIL with `ImportError` / `TypeError` on the new names.

- [ ] **Step 3: Implement the additions as listed in Interfaces**

`ADAPTERS` entries become `lambda seed: PolishBankruptcyAdapter()`, `lambda seed: TaiwanBankruptcyAdapter(seed=seed)`, `lambda seed: SyntheticCreditAdapter(seed=seed)`.

- [ ] **Step 4: Run the whole core suite and lint**

Run: `uv run pytest packages/core -m "not slow" && make lint`
Expected: all pass; the `_pipeline` helper in `test_thresholds.py` is unchanged.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): synthetic dataset adapter, seed-aware registry, calibrate_split, model dispatch, optional sensitivity/benchmark in thresholds"
```

---

### Task 3: Lineage foundations: ids, canonical JSON, models, database, migrations, test fixtures, logging

**Files:**
- Create: `packages/lineage/modelforge_lineage/ids.py`, `canonical.py`, `models.py`, `db.py`, `migrate.py`, `migrations/0001_initial.sql`, `testing.py`, `observability.py`
- Test: `packages/lineage/tests/conftest.py`, `test_canonical.py`, `test_migrate.py`, `test_observability.py`

**Interfaces:**
- Produces:
  - `ids.new_id(prefix: str) -> str` (`f"{prefix}_{uuid4().hex[:12]}"`).
  - `canonical.canonical_json(obj: Any) -> str` (`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`); `canonical.sha256_hex(data: bytes | str) -> str`.
  - `models.py` (all `StrictModel` from `modelforge_core.schemas`): `RunStatus = Literal["queued", "running", "awaiting_scope", "awaiting_model", "awaiting_signoff", "needs_attention", "completed", "rejected", "aborted"]`; `GateName = Literal["scope", "model", "signoff"]`; `QueueKind = Literal["start", "resume", "replay"]`; `StepStatus = Literal["running", "succeeded", "failed"]`; `Producer(server: str, tool: str, version: str)`; `ArtifactRef(id: str, kind: str, media_type: str, size_bytes: int, producer: Producer | None = None, summary: dict[str, Any] = {})`; `EventRecord(id: int, run_id: str, step_id: str | None, ts: str, type: str, payload: dict[str, Any], prev_hash: str, hash: str)`; `RunRecord(id, created_at: datetime, updated_at: datetime, requester: str, status: RunStatus, profile: str, brief_hash: str, brief_ref: str, dataset_ref: str, dataset_id: str | None, plan_ref: str | None, seed: int, cost_usd: float, cost_cap_usd: float, trace_id: str | None, manifest_hash: str | None, replay_of: str | None, error: dict[str, Any] | None)`; `StepRecord(id, run_id, node: str, attempt: int, started_at: datetime, finished_at: datetime | None, status: StepStatus, input_state_hash: str, output_state_hash: str | None)`; `ToolCallRecord(id, step_id, server, tool, version, input_hash, output_hash, duration_ms: int, artifact_ids: list[str])`; `ArtifactRow(id, run_id, kind, media_type, size_bytes, uri, producer_step_id: str | None, schema_version: str)`; `QueuedRun(run_id: str, kind: QueueKind)`; `GateDecision(gate: GateName, decision: str, approver: str, comment: str = "", plan: ModelingPlan | None = None, adjust_target: Literal["features", "build"] | None = None, conditions: list[str] = [])`; `PlanDiffEntry(path: str, old: Any, new: Any)`; `ApprovalRecord(id, run_id, gate: GateName, decision: str, approver: str, comment: str, payload: GateDecision, plan_diff: list[PlanDiffEntry] | None, conditions: list[str] | None, adjust_target: str | None, ts: datetime)`; `InventoryEntry(model_id, run_id, name, version: int, owner, intended_use, tier: int, status: str, champion_ref: str | None, validation_summary: dict[str, Any] | None, approved_at: datetime | None, approver: str | None, conditions: list[str] | None, next_review: date | None)`; `ChainReport(run_id, events: int, ok: bool, first_bad_id: int | None)`; `VerifyReport(run_id, chain: ChainReport, artifacts_checked: int, artifacts_missing: list[str], artifacts_corrupt: list[str], manifest_ok: bool, ok: bool)`.
  - `db.Database(url: str, *, min_size: int = 1, max_size: int = 4)` with `pool: ConnectionPool` (connection kwargs `autocommit=True, row_factory=dict_row, prepare_threshold=0`), `connection() -> Iterator[Connection]`, `transaction() -> Iterator[Connection]` (a pooled connection inside `conn.transaction()`), `close()`.
  - `migrate.apply(db: Database) -> list[int]` (versions applied in this call; reads `migrations/*.sql` in name order; each file runs inside one transaction followed by the `schema_migrations` insert); `migrate.TABLES: tuple[str, ...]` in truncation order: `("tool_calls", "steps", "events", "artifacts", "approvals", "findings", "model_inventory", "run_queue", "runs", "llm_calls", "prompts")`.
  - `testing.temporary_database(url: str) -> Iterator[str]` (context manager; connects to `url` with autocommit, `CREATE DATABASE modelforge_test_<8 hex>`, yields the URL with that database name, on exit terminates its backends and drops it). Pytest fixtures in the same module (loaded via `pytest_plugins = ["modelforge_lineage.testing"]` in each package's `conftest.py`): `database_url` (session: `MODELFORGE_DATABASE_URL` or the default; attempts a connection; `pytest.skip` with the error text, or `pytest.fail` when `MODELFORGE_REQUIRE_DB=1`), `database` (session: a `Database` on a temporary database with `apply` run, closed at the end), `db` (function: truncates `TABLES` with `RESTART IDENTITY CASCADE`, yields the same `Database`).
  - `observability.configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> None` (structlog JSON renderer, ISO timestamps, to `stream or sys.stderr`); `observability.get_logger(**bind: Any) -> structlog.stdlib.BoundLogger`.

`0001_initial.sql` creates every table of spec section 3.2 with these constraints: primary keys as listed; `run_queue.run_id`, `steps.run_id`, `events.run_id`, `artifacts.run_id`, `approvals.run_id`, `findings.run_id`, `model_inventory.run_id` reference `runs(id)`; `tool_calls.step_id` references `steps(id)`; `runs.replay_of` references `runs(id)`; checks on `run_queue.kind`, `steps.status`; `events` has `unique (run_id, hash)` and index `(run_id, id)`; indexes on `steps(run_id)`, `artifacts(run_id)`, `approvals(run_id, gate, ts)`, `tool_calls(step_id)`; `runs.created_at`/`updated_at`, `events.ts`, `steps.started_at`, `approvals.ts` default `now()`.

- [ ] **Step 1: Write the failing tests**

```python
# test_canonical.py
def test_canonical_json_is_sorted_compact_and_unicode() -> None:
    assert canonical_json({"b": 1, "a": [1, {"d": None, "c": "é"}]}) == '{"a":[1,{"c":"é","d":null}],"b":1}'
    assert sha256_hex("abc") == hashlib.sha256(b"abc").hexdigest()

def test_new_id_shape() -> None:
    assert re.fullmatch(r"run_[0-9a-f]{12}", new_id("run"))

# test_migrate.py
@pytest.mark.db
def test_apply_is_idempotent_and_creates_tables(database: Database) -> None:
    assert apply(database) == []                       # the session fixture already applied it
    with database.connection() as conn:
        names = {r["table_name"] for r in conn.execute(
            "select table_name from information_schema.tables where table_schema = 'public'")}
    assert set(TABLES) | {"schema_migrations"} <= names

@pytest.mark.db
def test_db_fixture_truncates_between_tests(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute("insert into prompts (name, version, hash, path) values ('p', 1, 'h', 'x')")
    with db.connection() as conn:
        assert conn.execute("select count(*) as n from prompts").fetchone()["n"] == 1

@pytest.mark.db
def test_db_fixture_truncates_between_tests_2(db: Database) -> None:
    with db.connection() as conn:
        assert conn.execute("select count(*) as n from prompts").fetchone()["n"] == 0

# test_observability.py
def test_logging_is_json_lines_on_given_stream() -> None:
    buf = io.StringIO(); configure_logging("INFO", stream=buf)
    get_logger(run_id="run_x").info("hello", n=1)
    record = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert record["event"] == "hello" and record["run_id"] == "run_x" and record["n"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/lineage -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the modules and the migration as listed in Interfaces**

`temporary_database` uses `psycopg.connect(url, autocommit=True)` and `sql.Identifier` for the database name; before `DROP DATABASE` it runs `select pg_terminate_backend(pid) from pg_stat_activity where datname = %s`.

- [ ] **Step 4: Run tests (Postgres up via `make db-up`) and lint; then run once with Postgres stopped to see the skip**

Run: `uv run pytest packages/lineage -v && make lint`
Expected: 5 passed. Then `make db-down && uv run pytest packages/lineage -v -rs` shows the three `db` tests skipped with the connection error in the reason; `MODELFORGE_REQUIRE_DB=1 uv run pytest packages/lineage/tests/test_migrate.py` fails. Restart with `make db-up`.

- [ ] **Step 5: Commit**

```bash
git add packages/lineage
git commit -m "feat(lineage): database, SQL migrations, record models, temporary-database fixtures, structured logging"
```

---

### Task 4: Content-addressed artifact store

**Files:**
- Create: `packages/lineage/modelforge_lineage/store.py`
- Test: `packages/lineage/tests/test_store.py`

**Interfaces:**
- Consumes: `ArtifactRef`, `Producer`, `sha256_hex`.
- Produces: `class ArtifactNotFound(KeyError)`; `class ArtifactStore: def __init__(self, root: Path)`; `put(data: bytes, *, kind: str, media_type: str, producer: Producer | None = None, summary: dict[str, Any] | None = None) -> ArtifactRef`; `get(artifact_id: str) -> bytes`; `exists(artifact_id: str) -> bool`; `uri(artifact_id: str) -> str` (`file://` absolute); `path(artifact_id: str) -> Path` (`root/<aa>/<bb>/<id>`); `put_model(model: BaseModel, *, kind: str, producer=None, summary=None) -> ArtifactRef` (bytes = `model.model_dump_json(indent=2).encode()`, media `application/json`); `get_model(artifact_id: str, model_type: type[T]) -> T`; `put_frame(frame: pd.DataFrame, *, kind: str, producer=None, summary=None) -> ArtifactRef` (pyarrow `Table.from_pandas(frame, preserve_index=False)`, `pq.write_table(..., compression="zstd")`, media `application/vnd.apache.parquet`); `get_frame(artifact_id: str) -> pd.DataFrame`. Constants `MEDIA_JSON`, `MEDIA_PARQUET`, `MEDIA_MARKDOWN = "text/markdown"`, `MEDIA_HTML = "text/html"`, `MEDIA_YAML = "application/yaml"`.

Writes go to `root/tmp/<uuid4 hex>` then `os.replace` into place; an existing file with the same id is left untouched.

- [ ] **Step 1: Write the failing tests**

```python
def test_put_get_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.put(b"hello", kind="brief", media_type="text/plain", summary={"n": 1})
    assert ref.id == hashlib.sha256(b"hello").hexdigest() and ref.size_bytes == 5
    assert store.path(ref.id) == tmp_path / ref.id[:2] / ref.id[2:4] / ref.id
    assert store.get(ref.id) == b"hello" and store.uri(ref.id).startswith("file://")
    assert store.put(b"hello", kind="other", media_type="text/plain").id == ref.id
    assert not list((tmp_path / "tmp").iterdir())

def test_get_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ArtifactNotFound):
        ArtifactStore(tmp_path).get("00" * 32)

def test_model_round_trip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.put_model(Thresholds(), kind="thresholds", producer=Producer(server="s", tool="t", version="1.0.0"))
    assert ref.media_type == "application/json" and ref.producer.tool == "t"
    assert store.get_model(ref.id, Thresholds) == Thresholds()

def test_frame_round_trip_is_byte_deterministic(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    frame = make_synthetic_credit_frame(300, seed=1)
    a = store.put_frame(frame, kind="dataset"); b = store.put_frame(frame.copy(), kind="dataset")
    assert a.id == b.id and a.media_type == "application/vnd.apache.parquet"
    back = store.get_frame(a.id)
    assert back.equals(frame) and list(back.dtypes) == list(frame.dtypes)

def test_partial_write_never_lands_under_a_valid_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ArtifactStore(tmp_path)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.put(b"data", kind="k", media_type="text/plain")
    assert not store.exists(hashlib.sha256(b"data").hexdigest())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/lineage/tests/test_store.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `store.py`**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/lineage/tests/test_store.py -v && make lint`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/lineage
git commit -m "feat(lineage): content-addressed artifact store with atomic writes and deterministic Parquet"
```

---

### Task 5: Events chain, runs, queue, steps

**Files:**
- Create: `packages/lineage/modelforge_lineage/events.py`, `runs.py`, `queue.py`, `steps.py`
- Test: `packages/lineage/tests/test_events.py`, `test_runs.py`, `test_queue.py`, `test_steps.py`

**Interfaces:**
- Consumes: `Database`, models, `canonical_json`, `sha256_hex`, `new_id`.
- Produces:
  - `events.GENESIS = "0" * 64`; `event_hash(prev_hash: str, body: dict[str, Any]) -> str` (`sha256_hex(prev_hash + canonical_json(body))`, body = `{"type", "ts", "step_id", "payload"}`); `append_event(conn, run_id: str, type: str, payload: dict[str, Any], *, step_id: str | None = None) -> EventRecord` (requires an open transaction; locks the run row with `select id from runs where id = %s for update`; `ts = datetime.now(UTC).isoformat(timespec="microseconds")`; `prev_hash` = hash of the run's latest event or `GENESIS`); `list_events(conn, run_id) -> list[EventRecord]` (by id); `verify_chain(conn, run_id) -> ChainReport`.
  - `runs.RunNotFound(KeyError)`; `create_run(conn, *, run_id: str, requester: str, profile: str, brief_hash: str, brief_ref: str, dataset_ref: str, seed: int, replay_of: str | None = None, cost_cap_usd: float = 10.0) -> RunRecord` (status `queued`; appends `run.created` with the record's fields); `get_run(conn, run_id) -> RunRecord`; `list_runs(conn, *, limit: int = 50) -> list[RunRecord]`; `set_status(conn, run_id, status: RunStatus) -> None` (appends `run.status_changed {"from", "to"}`; also bumps `updated_at`); `set_dataset_id`, `set_plan_ref`, `set_manifest_hash` (`(conn, run_id, value: str)`), `set_error(conn, run_id, error: dict[str, Any] | None)`.
  - `queue.enqueue(conn, run_id, kind: QueueKind) -> None` (upsert; appends `run.enqueued {"kind"}`); `claim(conn, worker_id: str, *, run_id: str | None = None) -> QueuedRun | None` (the spec's `UPDATE ... SKIP LOCKED` statement, with `and run_id = %s` in the subquery when `run_id` is given); `release(conn, run_id) -> None`.
  - `steps.start_step(conn, run_id, node: str, input_state_hash: str) -> StepRecord` (attempt = 1 + count of that node's steps for the run); `finish_step(conn, step_id, *, status: StepStatus, output_state_hash: str | None) -> None`; `list_steps(conn, run_id) -> list[StepRecord]`; `insert_tool_call(conn, record: ToolCallRecord) -> None`; `list_tool_calls(conn, run_id) -> list[ToolCallRecord]`; `insert_artifact(conn, *, run_id: str, ref: ArtifactRef, uri: str, producer_step_id: str | None, schema_version: str = "1") -> None` (`on conflict (id, run_id) do nothing`); `list_artifacts(conn, run_id) -> list[ArtifactRow]`.

- [ ] **Step 1: Write the failing tests** (all `@pytest.mark.db`; a helper `_run(conn, run_id="run_a")` calls `create_run` with fixed values)

```python
# test_events.py
def test_chain_links_and_verifies(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn); e1 = append_event(conn, "run_a", "x", {"k": 1}); e2 = append_event(conn, "run_a", "y", {}, step_id=None)
    assert e1.prev_hash != GENESIS            # run.created came first
    assert e2.prev_hash == e1.hash and e2.hash == event_hash(e1.hash, {"type": "y", "ts": e2.ts, "step_id": None, "payload": {}})
    with db.connection() as conn:
        assert verify_chain(conn, "run_a") == ChainReport(run_id="run_a", events=3, ok=True, first_bad_id=None)

def test_tampered_payload_and_deleted_event_are_detected(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn); ids = [append_event(conn, "run_a", "t", {"i": i}).id for i in range(3)]
    with db.transaction() as conn:
        conn.execute("update events set payload = '{\"i\": 99}' where id = %s", (ids[1],))
    with db.connection() as conn:
        assert verify_chain(conn, "run_a").first_bad_id == ids[1]
    with db.transaction() as conn:
        conn.execute("update events set payload = '{\"i\": 1}' where id = %s", (ids[1],))
        conn.execute("delete from events where id = %s", (ids[1],))
    with db.connection() as conn:
        report = verify_chain(conn, "run_a")
        assert report.ok is False and report.first_bad_id == ids[2]

def test_append_requires_transaction(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn)
    with db.connection() as conn, pytest.raises(RuntimeError):
        append_event(conn, "run_a", "x", {})
```

```python
# test_runs.py
def test_create_get_and_status_events(db: Database) -> None:
    with db.transaction() as conn:
        rec = _run(conn); set_status(conn, "run_a", "running"); set_dataset_id(conn, "run_a", "d" * 64)
        got = get_run(conn, "run_a")
    assert rec.status == "queued" and got.status == "running" and got.dataset_id == "d" * 64 and got.replay_of is None
    with db.connection() as conn:
        types = [e.type for e in list_events(conn, "run_a")]
    assert types == ["run.created", "run.status_changed"]
    with db.connection() as conn, pytest.raises(RunNotFound):
        get_run(conn, "run_zz")
```

```python
# test_queue.py
def test_claim_order_release_and_specific(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn, "run_a"); _run(conn, "run_b"); enqueue(conn, "run_a", "start"); enqueue(conn, "run_b", "start")
    with db.transaction() as conn:
        assert claim(conn, "w1", run_id="run_b") == QueuedRun(run_id="run_b", kind="start")
        assert claim(conn, "w1") == QueuedRun(run_id="run_a", kind="start")
        assert claim(conn, "w1") is None
        release(conn, "run_a"); release(conn, "run_b")
        enqueue(conn, "run_a", "resume")
        assert claim(conn, "w1").kind == "resume"

def test_concurrent_claims_take_different_runs(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn, "run_a"); _run(conn, "run_b"); enqueue(conn, "run_a", "start"); enqueue(conn, "run_b", "start")
    first_claimed = threading.Event(); second_done = threading.Event(); got: dict[str, str | None] = {}
    def worker1() -> None:
        with db.transaction() as conn:
            got["w1"] = claim(conn, "w1").run_id; first_claimed.set(); second_done.wait(5)   # hold the lock open
    def worker2() -> None:
        first_claimed.wait(5)
        with db.transaction() as conn:
            q = claim(conn, "w2"); got["w2"] = q.run_id if q else None
        second_done.set()
    t1, t2 = threading.Thread(target=worker1), threading.Thread(target=worker2); t1.start(); t2.start(); t1.join(10); t2.join(10)
    assert {got["w1"], got["w2"]} == {"run_a", "run_b"}
```

```python
# test_steps.py
def test_steps_attempts_tool_calls_and_artifacts(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn); s1 = start_step(conn, "run_a", "profile", "h0"); finish_step(conn, s1.id, status="failed", output_state_hash=None)
        s2 = start_step(conn, "run_a", "profile", "h0")
        insert_tool_call(conn, ToolCallRecord(id=new_id("tc"), step_id=s2.id, server="data-profiler", tool="profile_dataset", version="1.0.0", input_hash="i", output_hash="o", duration_ms=5, artifact_ids=["a" * 64]))
        ref = ArtifactRef(id="a" * 64, kind="profile", media_type="application/json", size_bytes=3)
        insert_artifact(conn, run_id="run_a", ref=ref, uri="file:///x", producer_step_id=s2.id); insert_artifact(conn, run_id="run_a", ref=ref, uri="file:///x", producer_step_id=s2.id)
        finish_step(conn, s2.id, status="succeeded", output_state_hash="h1")
    with db.connection() as conn:
        steps = list_steps(conn, "run_a"); calls = list_tool_calls(conn, "run_a"); arts = list_artifacts(conn, "run_a")
    assert [s.attempt for s in steps] == [1, 2] and steps[1].status == "succeeded" and steps[1].output_state_hash == "h1"
    assert len(calls) == 1 and calls[0].artifact_ids == ["a" * 64] and len(arts) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/lineage/tests/test_events.py packages/lineage/tests/test_runs.py packages/lineage/tests/test_queue.py packages/lineage/tests/test_steps.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the four modules**

`append_event` raises `RuntimeError("append_event requires an open transaction")` when `conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE`.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/lineage -v && make lint`
Expected: all pass (the concurrency test completes in well under 5 s).

- [ ] **Step 5: Commit**

```bash
git add packages/lineage
git commit -m "feat(lineage): hash-chained events, runs, skip-locked queue, steps and tool-call rows"
```

---

### Task 6: Approvals with four-eyes, inventory, verify, façade

**Files:**
- Create: `packages/lineage/modelforge_lineage/approvals.py`, `inventory.py`, `verify.py`, `facade.py`
- Test: `packages/lineage/tests/test_approvals.py`, `test_inventory.py`, `test_verify.py`

**Interfaces:**
- Consumes: Tasks 3 to 5; `ModelingPlan` from core.
- Produces:
  - `approvals.VALID_DECISIONS: dict[GateName, frozenset[str]]` = `scope: {approve, edit, reject}`, `model: {accept, adjust, reject}`, `signoff: {approve, approve_with_conditions, reject}`; exceptions `ApprovalError(ValueError)` with subclasses `GateMismatchError`, `FourEyesError`, `LoopBoundError`, `InvalidDecisionError`; `plan_diff(old: ModelingPlan, new: ModelingPlan) -> list[PlanDiffEntry]` (dotted paths over `model_dump(mode="json")`, lists compared whole); `record_approval(conn, run_id: str, decision: GateDecision, *, current_plan: ModelingPlan | None, loop_bound: int = 3) -> ApprovalRecord` (checks: run status equals `f"awaiting_{decision.gate}"` else `GateMismatchError`; decision in `VALID_DECISIONS[gate]` else `InvalidDecisionError`; `edit`/`adjust` require `plan`, `adjust` requires `adjust_target`, `approve_with_conditions` requires non-empty `conditions`, else `InvalidDecisionError`; gates `model`/`signoff` require `approver != runs.requester` else `FourEyesError`; `adjust` with `loop_bound` or more earlier `adjust` approvals on the run raises `LoopBoundError`; `plan_diff` filled when `plan` is given and `current_plan` is not None; appends `gate.decided {"gate", "decision", "approver"}`); `list_approvals(conn, run_id, *, gate: GateName | None = None) -> list[ApprovalRecord]` (by `ts`, then id).
  - `inventory.create_stub(conn, *, run_id, name, owner, intended_use, tier: int) -> InventoryEntry` (`status="draft"`, `version = 1 + count(name)`, `model_id = f"{name}@v{version}"`); `finalise(conn, model_id, *, status: str, champion_ref: str | None, validation_summary: dict | None, approver: str | None, conditions: list[str] | None, approved_at: datetime | None) -> InventoryEntry`; `get_entry(conn, model_id) -> InventoryEntry`; `get_for_run(conn, run_id) -> InventoryEntry | None`.
  - `verify.verify_run(conn, store: ArtifactStore, run_id: str) -> VerifyReport` (chain report; every `artifacts` row of the run: missing when `not store.exists`, corrupt when `sha256_hex(store.get(id)) != id`; `manifest_ok` when `runs.manifest_hash` is set, its file exists, parses as JSON with a `refs` object, and every `refs[*].id` appears in the run's artifact rows; `ok = chain.ok and not missing and not corrupt and manifest_ok`).
  - `facade.Lineage(db: Database)` with `db`, `connection()`, `transaction()` passthroughs; the repositories are used as modules.

- [ ] **Step 1: Write the failing tests** (`@pytest.mark.db`; helper `_awaiting(conn, gate)` creates `run_a` with requester `alice` and sets status `awaiting_<gate>`; `PLAN = ModelingPlan(...)` a module constant built from the SP1 schema defaults)

```python
# test_approvals.py
def test_scope_edit_records_diff_and_event(db: Database) -> None:
    edited = PLAN.model_copy(update={"feature_policy": PLAN.feature_policy.model_copy(update={"max_features": 12})})
    with db.transaction() as conn:
        _awaiting(conn, "scope")
        rec = record_approval(conn, "run_a", GateDecision(gate="scope", decision="edit", approver="alice", plan=edited), current_plan=PLAN)
    assert rec.plan_diff == [PlanDiffEntry(path="feature_policy.max_features", old=20, new=12)]
    with db.connection() as conn:
        assert list_events(conn, "run_a")[-1].type == "gate.decided" and list_approvals(conn, "run_a", gate="scope") == [rec]

def test_four_eyes_on_model_and_signoff(db: Database) -> None:
    for gate, decision in (("model", "accept"), ("signoff", "approve")):
        with db.transaction() as conn:
            _awaiting(conn, gate)
            with pytest.raises(FourEyesError):
                record_approval(conn, "run_a", GateDecision(gate=gate, decision=decision, approver="alice"), current_plan=PLAN)
            record_approval(conn, "run_a", GateDecision(gate=gate, decision=decision, approver="bob"), current_plan=PLAN)
            conn.execute("delete from approvals"); conn.execute("delete from events"); conn.execute("delete from runs")

def test_record_approval_rejects_wrong_gate(db: Database) -> None:
    with db.transaction() as conn:
        _awaiting(conn, "model")
        with pytest.raises(GateMismatchError):
            record_approval(conn, "run_a", GateDecision(gate="scope", decision="approve", approver="bob"), current_plan=PLAN)
        set_status(conn, "run_a", "completed")
        with pytest.raises(GateMismatchError):
            record_approval(conn, "run_a", GateDecision(gate="model", decision="accept", approver="bob"), current_plan=PLAN)
    with db.connection() as conn:
        assert list_approvals(conn, "run_a") == []

def test_invalid_decisions_and_loop_bound(db: Database) -> None:
    with db.transaction() as conn:
        _awaiting(conn, "model")
        with pytest.raises(InvalidDecisionError):
            record_approval(conn, "run_a", GateDecision(gate="model", decision="approve", approver="bob"), current_plan=PLAN)
        with pytest.raises(InvalidDecisionError):            # adjust without plan/target
            record_approval(conn, "run_a", GateDecision(gate="model", decision="adjust", approver="bob"), current_plan=PLAN)
        adjust = GateDecision(gate="model", decision="adjust", approver="bob", plan=PLAN, adjust_target="build")
        for _ in range(3):
            record_approval(conn, "run_a", adjust, current_plan=PLAN)
        with pytest.raises(LoopBoundError):
            record_approval(conn, "run_a", adjust, current_plan=PLAN)
```

```python
# test_inventory.py
def test_versioning_and_finalise(db: Database) -> None:
    with db.transaction() as conn:
        _run(conn, "run_a"); _run(conn, "run_b")
        e1 = create_stub(conn, run_id="run_a", name="pd", owner="alice", intended_use="demo", tier=2)
        e2 = create_stub(conn, run_id="run_b", name="pd", owner="alice", intended_use="demo", tier=2)
        done = finalise(conn, e1.model_id, status="approved_with_conditions", champion_ref="c" * 64, validation_summary={"overall": "warn"}, approver="bob", conditions=["monitor PSI"], approved_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert (e1.model_id, e2.model_id, e1.status) == ("pd@v1", "pd@v2", "draft")
    assert done.status == "approved_with_conditions" and done.conditions == ["monitor PSI"]
    with db.connection() as conn:
        assert get_for_run(conn, "run_a") == done and get_for_run(conn, "run_zz") is None
```

```python
# test_verify.py
def test_verify_run_reports_tampered_artifact_and_event(db: Database, tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    with db.transaction() as conn:
        _run(conn)
        good = store.put(b"good", kind="profile", media_type="text/plain"); insert_artifact(conn, run_id="run_a", ref=good, uri=store.uri(good.id), producer_step_id=None)
        manifest = store.put_model(_Manifest(refs={"profile": good}), kind="manifest"); insert_artifact(conn, run_id="run_a", ref=manifest, uri=store.uri(manifest.id), producer_step_id=None)
        set_manifest_hash(conn, "run_a", manifest.id)
    with db.connection() as conn:
        assert verify_run(conn, store, "run_a").ok is True
    store.path(good.id).write_bytes(b"evil")
    with db.transaction() as conn:
        conn.execute("update events set payload = '{}' where id = (select min(id) from events)")
    with db.connection() as conn:
        report = verify_run(conn, store, "run_a")
    assert report.ok is False and report.artifacts_corrupt == [good.id] and report.chain.first_bad_id is not None
    store.path(good.id).unlink()
    with db.connection() as conn:
        assert verify_run(conn, store, "run_a").artifacts_missing == [good.id]
```

`_Manifest` is a test-local `StrictModel` with `refs: dict[str, ArtifactRef]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/lineage/tests/test_approvals.py packages/lineage/tests/test_inventory.py packages/lineage/tests/test_verify.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the four modules**

- [ ] **Step 4: Run the lineage suite and lint**

Run: `uv run pytest packages/lineage -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/lineage
git commit -m "feat(lineage): approvals with four-eyes and loop bounds, model inventory, run verification, facade"
```

---
### Task 7: MCP common envelope, data-profiler server, `modelforge-mcp` entrypoint

**Files:**
- Create: `packages/mcp/modelforge_mcp/common.py`, `schemas.py`, `registry.py`, `servers/__init__.py`, `servers/data_profiler.py`, `__main__.py`
- Test: `packages/mcp/tests/conftest.py`, `test_common.py`, `test_data_profiler.py`, `test_main.py`

**Interfaces:**
- Consumes: `ArtifactStore`, `ArtifactRef`, `Producer`, `ArtifactNotFound`, `configure_logging`, `get_logger` (lineage); `modelforge_core.datasets.registry.load_dataset`, `profile`, `check_leakage`, `compute_psi`, `make_splits`, `ChecksumMismatchError`.
- Produces:
  - `schemas.DatasetRef(StrictModel)`: `adapter: str`, `seed: int = 42`; `def as_string(self) -> str` (`adapter` or `adapter@seed` when seed != 42); `@classmethod parse(cls, text: str) -> DatasetRef`.
  - `common.ServerName = Literal["data-profiler", "credit-modeling", "validation", "doc-renderer"]`; `ServerSettings(StrictModel)`: `artifact_dir: Path`, `data_dir: Path`, `templates_dir: Path`; `settings_from_env(env: Mapping[str, str] | None = None) -> ServerSettings` (`MODELFORGE_ARTIFACT_DIR` default `./artifacts`, `MODELFORGE_DATA_DIR` default `./data`, `MODELFORGE_TEMPLATES_DIR` default `./templates`); `ToolErrorCode = Literal["invalid_input", "artifact_not_found", "dataset_unavailable", "internal"]`; `ToolErrorPayload(StrictModel)`: `code: ToolErrorCode`, `message: str`; `fail(code: ToolErrorCode, message: str) -> NoReturn` (raises `mcp.server.mcpserver.exceptions.ToolError(payload.model_dump_json())`); `class ToolContext: store: ArtifactStore; settings: ServerSettings; server: ServerName; log`; `load_model(ctx, artifact_id: str, model_type: type[T]) -> T` and `load_frame(ctx, artifact_id: str) -> pd.DataFrame` (map `ArtifactNotFound` to `fail("artifact_not_found", ...)`); `producer(ctx, tool: str, version: str) -> Producer`; `register_tool(server: MCPServer, ctx: ToolContext, *, name: str, version: str, description: str, fn: Callable[[ToolContext, I], O], input_model: type[I], output_model: type[O]) -> None` (registers a wrapper taking `request: I` and returning `O`, with `meta={"version": version}`; the wrapper logs start and end with `tool`, `version`, `duration_ms`, maps `ArtifactNotFound` to `artifact_not_found`, `ChecksumMismatchError` to `dataset_unavailable`, `ValueError`/`KeyError` to `invalid_input`, `ToolError` passes through, any other `Exception` to `internal`); `make_server(name: ServerName, version: str, instructions: str) -> MCPServer`. The wrapper that `register_tool` builds has `__annotations__ = {"request": input_model, "return": output_model}` so the SDK derives both schemas from the Pydantic models.
  - `registry.ToolSpec(StrictModel)`: `server: ServerName`, `tool: str`, `version: str`, `input_model: type[StrictModel]`, `output_model: type[StrictModel]` (arbitrary types allowed); `TOOLS: dict[tuple[ServerName, str], ToolSpec]`; `SERVERS: dict[ServerName, Callable[[ServerSettings], MCPServer]]`; `build_server(name: ServerName, settings: ServerSettings) -> MCPServer`. Each server module exposes `SERVER_NAME`, `SERVER_VERSION = "1.0.0"`, `TOOL_SPECS: list[ToolSpec]` and `build(settings: ServerSettings) -> MCPServer`; `registry.py` imports the server modules and assembles `TOOLS` and `SERVERS`.
  - `servers/data_profiler.py` models: `LoadDatasetInput(dataset_ref: DatasetRef)`, `LoadDatasetOutput(dataset: ArtifactRef, dictionary: ArtifactRef, oot_capable: bool, n_rows: int)`, `ProfileDatasetInput(dataset_id: str)`, `CheckLeakageInput(dataset_id: str, auc_threshold: float = 0.9)`, `ComputePsiInput(dataset_id: str, base_vintages: list[int], compare_vintages: list[int], features: list[str] | None = None, n_bins: int = 10)`, `MakeSplitsInput(dataset_id: str, strategy: Literal["vintage", "stratified_random"], oot_vintages: list[int] = [], val_share: float = 0.2, seed: int)`. Tool functions `load_dataset(ctx, req) -> LoadDatasetOutput`, `profile_dataset(ctx, req) -> ArtifactRef`, `check_leakage(ctx, req) -> ArtifactRef`, `compute_psi(ctx, req) -> ArtifactRef`, `make_splits(ctx, req) -> ArtifactRef`; artifact kinds `dataset` (`put_frame`, summary `{"adapter", "seed", "n_rows", "oot_capable"}`), `column_dictionary`, `profile` (summary `n_rows, n_features, target_rate`), `leakage` (summary `flagged: list[str], duplicate_rows`), `psi` (summary `max_psi, worst_feature`), `split_manifest` (summary `rows: {split: n}, oot_available`). `profile_dataset` passes `dataset=` the dataset summary's adapter name.
  - `__main__.main(argv: Sequence[str] | None = None) -> int`: `modelforge-mcp <server>` calls `configure_logging`, `build_server(name, settings_from_env())`, `server.run("stdio")`; unknown server prints the four names and returns 2. (`contracts` and `goldens` subcommands arrive in Task 11.)

- [ ] **Step 1: Write `conftest.py` and the failing tests**

`conftest.py`: fixtures `settings(tmp_path) -> ServerSettings` (artifact dir and data dir under `tmp_path`, templates dir = repo `templates/`), `call(settings)` returning a helper `def call(server_name, tool, request: StrictModel) -> CallToolResult` that runs `asyncio.run` over `async with Client(build_server(server_name, settings)) as c: return await c.call_tool(tool, {"request": request.model_dump(mode="json")})`, and `store(settings) -> ArtifactStore`. The synthetic dataset is loaded once per test module through `load_dataset` with `DatasetRef(adapter="synthetic_credit", seed=42)`.

```python
# test_common.py
def test_dataset_ref_string_forms() -> None:
    assert DatasetRef.parse("polish_bankruptcy") == DatasetRef(adapter="polish_bankruptcy")
    assert DatasetRef.parse("taiwan_bankruptcy@7").as_string() == "taiwan_bankruptcy@7"
    assert DatasetRef(adapter="x").as_string() == "x"

def test_settings_from_env_defaults_and_overrides(tmp_path: Path) -> None:
    assert settings_from_env({}) == ServerSettings(artifact_dir=Path("artifacts"), data_dir=Path("data"), templates_dir=Path("templates"))
    assert settings_from_env({"MODELFORGE_ARTIFACT_DIR": str(tmp_path)}).artifact_dir == tmp_path

def test_errors_are_typed_payloads(call, settings) -> None:
    r = call("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id="0" * 64))
    assert r.is_error and ToolErrorPayload.model_validate_json(r.content[0].text.split(": ", 1)[1]).code == "artifact_not_found"
```

```python
# test_data_profiler.py
def test_load_dataset_stores_parquet_and_dictionary(call, store) -> None:
    out = LoadDatasetOutput.model_validate(call("data-profiler", "load_dataset", LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit"))).structured_content)
    assert out.n_rows == 4000 and out.oot_capable and out.dataset.kind == "dataset" and out.dataset.producer == Producer(server="data-profiler", tool="load_dataset", version="1.0.0")
    assert store.get_model(out.dictionary.id, ColumnDictionary).feature_names == [f"f_{i}" for i in range(1, 7)]
    again = LoadDatasetOutput.model_validate(call("data-profiler", "load_dataset", LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit"))).structured_content)
    assert again.dataset.id == out.dataset.id

def test_profile_leakage_psi_splits(call, store, dataset_id) -> None:
    prof = ArtifactRef.model_validate(call("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id=dataset_id)).structured_content)
    assert store.get_model(prof.id, ProfileArtifact).n_rows == 4000 and prof.summary["n_rows"] == 4000
    leak = ArtifactRef.model_validate(call("data-profiler", "check_leakage", CheckLeakageInput(dataset_id=dataset_id)).structured_content)
    assert leak.summary["flagged"] == []
    psi = ArtifactRef.model_validate(call("data-profiler", "compute_psi", ComputePsiInput(dataset_id=dataset_id, base_vintages=[1, 2, 3, 4], compare_vintages=[5])).structured_content)
    assert store.get_model(psi.id, PSIReport).compare_vintages == [5] and psi.summary["max_psi"] < 0.1
    split = ArtifactRef.model_validate(call("data-profiler", "make_splits", MakeSplitsInput(dataset_id=dataset_id, strategy="vintage", oot_vintages=[5], seed=42)).structured_content)
    manifest = store.get_model(split.id, SplitManifest)
    assert manifest.oot_available and split.summary["rows"]["oot"] == len(manifest.ids("oot"))

def test_make_splits_missing_vintage_is_invalid_input(call, dataset_id) -> None:
    r = call("data-profiler", "make_splits", MakeSplitsInput(dataset_id=dataset_id, strategy="vintage", oot_vintages=[99], seed=1))
    assert r.is_error and '"code": "invalid_input"' in r.content[0].text.replace('"code":"', '"code": "')

def test_tool_versions_are_published(settings) -> None:
    async def _list():
        async with Client(build_server("data-profiler", settings)) as c: return (await c.list_tools()).tools
    tools = asyncio.run(_list())
    assert {t.name for t in tools} == {"load_dataset", "profile_dataset", "check_leakage", "compute_psi", "make_splits"}
    assert all(t.meta == {"version": "1.0.0"} and t.output_schema is not None for t in tools)
```

```python
# test_main.py
def test_main_rejects_unknown_server(capsys) -> None:
    assert main(["nope"]) == 2 and "data-profiler" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/mcp -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `common.py`, `schemas.py`, `registry.py`, `servers/data_profiler.py`, `__main__.py`**

`load_dataset` calls `modelforge_core.datasets.registry.load_dataset(ref.adapter, ctx.settings.data_dir, seed=ref.seed)`; an unknown adapter's `KeyError` maps to `invalid_input` through the wrapper.

- [ ] **Step 4: Run tests and lint; then start the server standalone and list its tools**

Run: `uv run pytest packages/mcp -v && make lint`
Expected: 8 passed.

Write `packages/mcp/tests/standalone_check.py` (not collected by pytest: no `test_` prefix) that opens `Client(StdioServerParameters(command="uv", args=["run", "modelforge-mcp", "data-profiler"]))`, prints the tool names one per line and exits 0.
Run: `MODELFORGE_ARTIFACT_DIR=/tmp/mf-artifacts uv run python packages/mcp/tests/standalone_check.py`
Expected: the five tool names on stdout; nothing else on stdout.

- [ ] **Step 5: Commit**

```bash
git add packages/mcp
git commit -m "feat(mcp): shared tool envelope, data-profiler server, modelforge-mcp entrypoint"
```

---

### Task 8: credit-modeling server

**Files:**
- Create: `packages/mcp/modelforge_mcp/servers/credit_modeling.py`
- Modify: `packages/mcp/modelforge_mcp/registry.py`
- Test: `packages/mcp/tests/test_credit_modeling.py`

**Interfaces:**
- Consumes: Task 7 envelope; core `fit_binning`, `transform_woe`, `select_features`, `fit_woe_logistic`, `fit_woe_logistic_stepwise`, `fit_gbm`, `calibrate_split`, `score_frame`, `apply_split`, `parse_model`, `predict_model`.
- Produces (`SERVER_NAME = "credit-modeling"`): `BinFeaturesInput(dataset_id: str, split_id: str, features: list[str], fit_split: Split = "train", method: Literal["quantile", "tree"] = "quantile", max_bins: int = 10, min_bin_share: float = 0.05, monotonic: bool = True)` -> `ArtifactRef(binning_tables)` summary `{"n_features", "iv": {feature: iv}}`; `SelectFeaturesInput(dataset_id, split_id, binning_id, fit_split="train", iv_min: float, iv_max: float, corr_max: float, max_features: int | None = None)` -> `ArtifactRef(feature_selection)` summary `{"selected", "n_dropped"}`; `FitLogisticInput(dataset_id, split_id, binning_id, selection_id, model_name: str, fit_split="train", stepwise: bool = True, penalty: Literal["l2", "none"] = "l2", c: float = 1.0)` -> `FitLogisticOutput(scorecard: ArtifactRef, selection: ArtifactRef)` (scorecard summary `{"features", "intercept", "fit_method", "n_wrong_sign_drops"}`; the selection artifact is the input selection with `selected` = the scorecard's features and `dropped` extended by the wrong-sign drops); `FitGbmInput(dataset_id, split_id, features: list[str], model_name: str, seed: int, params: dict[str, float | int | str | bool] | None = None, fit_split="train", val_split: Split | None = "val", early_stopping_rounds: int = 30)` -> `ArtifactRef(gbm_model)` summary `{"num_trees", "features"}`; `CalibrateInput(model_id: str, dataset_id, split_id, fit_split: Split = "val", method: Literal["platt", "isotonic", "none"] = "platt")` -> `ArtifactRef(calibration_map)` summary `{"method", "platt_a", "platt_b"}`; `ScoreInput(model_id: str, dataset_id, split_id, calibration_id: str | None = None)` -> `ArtifactRef(scores)` (Parquet of `SCORES_COLUMNS`; summary `{"rows": {split: n}, "model_name"}`). `model_id` is parsed with `parse_model`; a non-model artifact maps to `invalid_input`.

- [ ] **Step 1: Write the failing tests**

Module fixtures build `dataset_id` and `split_id` through the data-profiler tools (`vintage`, `oot_vintages=[5]`, `seed=42`).

```python
def test_bin_select_fit_chain(call, store, dataset_id, split_id) -> None:
    binning = ArtifactRef.model_validate(call("credit-modeling", "bin_features_woe", BinFeaturesInput(dataset_id=dataset_id, split_id=split_id, features=[f"f_{i}" for i in range(1, 7)])).structured_content)
    tables = store.get_model(binning.id, BinningTables)
    assert [f.feature for f in tables.features] == [f"f_{i}" for i in range(1, 7)] and binning.summary["iv"]["f_1"] > 0.1
    selection = ArtifactRef.model_validate(call("credit-modeling", "select_features", SelectFeaturesInput(dataset_id=dataset_id, split_id=split_id, binning_id=binning.id, iv_min=0.02, iv_max=0.5, corr_max=0.7, max_features=20)).structured_content)
    assert set(selection.summary["selected"]) >= {"f_1", "f_2", "f_3"}
    fit = FitLogisticOutput.model_validate(call("credit-modeling", "fit_logistic_pd", FitLogisticInput(dataset_id=dataset_id, split_id=split_id, binning_id=binning.id, selection_id=selection.id, model_name="m")).structured_content)
    sc = store.get_model(fit.scorecard.id, Scorecard); merged = store.get_model(fit.selection.id, FeatureSelection)
    assert sc.fit_method == "stepwise_wrong_sign" and merged.selected == [f.feature for f in sc.features]
    assert all(f.coefficient < 0 for f in sc.features) and fit.scorecard.summary["features"] == merged.selected

def test_gbm_calibrate_score(call, store, dataset_id, split_id, fitted) -> None:      # `fitted`: namespace with `binning`, `selection` (the input selection ref) and `scorecard` (the fit_logistic_pd scorecard ref)
    gbm = ArtifactRef.model_validate(call("credit-modeling", "fit_gbm_challenger", FitGbmInput(dataset_id=dataset_id, split_id=split_id, features=fitted.scorecard.summary["features"], model_name="g", seed=42)).structured_content)
    assert gbm.summary["num_trees"] >= 1 and store.get_model(gbm.id, GBMModel).model_text.startswith("tree")
    cal = ArtifactRef.model_validate(call("credit-modeling", "calibrate", CalibrateInput(model_id=fitted.scorecard.id, dataset_id=dataset_id, split_id=split_id)).structured_content)
    assert store.get_model(cal.id, CalibrationMap).fitted_on == "val" and cal.summary["method"] == "platt"
    champ = ArtifactRef.model_validate(call("credit-modeling", "score", ScoreInput(model_id=fitted.scorecard.id, dataset_id=dataset_id, split_id=split_id, calibration_id=cal.id)).structured_content)
    chal = ArtifactRef.model_validate(call("credit-modeling", "score", ScoreInput(model_id=gbm.id, dataset_id=dataset_id, split_id=split_id)).structured_content)
    frame = store.get_frame(champ.id)
    assert list(frame.columns) == list(SCORES_COLUMNS) and set(frame["split"]) == {"train", "val", "oot"} and champ.summary["rows"]["oot"] > 0
    assert store.get_frame(chal.id)["pd"].between(0, 1).all()

def test_score_rejects_non_model_artifact(call, dataset_id, split_id) -> None:
    r = call("credit-modeling", "score", ScoreInput(model_id=split_id, dataset_id=dataset_id, split_id=split_id))
    assert r.is_error and "invalid_input" in r.content[0].text

def test_fit_without_stepwise_keeps_selection(call, store, dataset_id, split_id, fitted) -> None:
    fit = FitLogisticOutput.model_validate(call("credit-modeling", "fit_logistic_pd", FitLogisticInput(dataset_id=dataset_id, split_id=split_id, binning_id=fitted.binning.id, selection_id=fitted.selection.id, model_name="m", stepwise=False)).structured_content)
    assert fit.selection.id == fitted.selection.id and store.get_model(fit.scorecard.id, Scorecard).fit_method == "single"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/mcp/tests/test_credit_modeling.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `servers/credit_modeling.py` and register it**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/mcp -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/mcp
git commit -m "feat(mcp): credit-modeling server (binning, selection, scorecard, GBM, calibration, scoring)"
```

---

### Task 9: validation server

**Files:**
- Create: `packages/mcp/modelforge_mcp/servers/validation.py`
- Modify: `packages/mcp/modelforge_mcp/registry.py`
- Test: `packages/mcp/tests/test_validation.py`

**Interfaces:**
- Consumes: core `discrimination_metrics`, `calibration_tests`, `stability_tests`, `sensitivity_analysis`, `benchmark`, `evaluate_thresholds`; Tasks 7 and 8 for fixtures.
- Produces (`SERVER_NAME = "validation"`): `DiscriminationInput(scores_id: str, model_name: str, bootstrap_samples: int = 200, seed: int)` -> `ArtifactRef(discrimination)` summary `{"auc": {split: auc}}`; `CalibrationTestsInput(scores_id, split: Split, alpha: float = 0.05, n_grades: int = 10)` -> `ArtifactRef(calibration_tests)` summary `{"n_grades", "grades_failed", "hl_p_value", "eo_ratio"}`; `StabilityInput(scores_id, dataset_id, binning_id, features: list[str], base_split: Split = "train", compare_split: Split, n_bins: int = 10)` -> `ArtifactRef(stability)` summary `{"score_psi", "max_csi"}`; `SensitivityInput(model_id: str, binning_id, dataset_id, shifts: list[int] = [-1, 1])` -> `ArtifactRef(sensitivity)` summary `{"all_signs_ok", "all_monotone"}` (`model_id` must parse to a `Scorecard`, else `invalid_input`); `BenchmarkInput(champion_scores_id, challenger_scores_id, champion: str, challenger: str, gap_threshold: float, bootstrap_samples: int = 200, seed: int)` -> `ArtifactRef(benchmark)` summary `{"decided_on", "auc_delta", "champion_within_gap"}`; `EvaluateThresholdsInput(model_name: str, thresholds: Thresholds, discrimination_id, calibration_id, stability_id, sensitivity_id: str | None, benchmark_id: str | None, selection_id, binning_id, oot_available: bool)` -> `ArtifactRef(validation_report)` summary `{"overall", "statuses": {test_id: status}}`. Non-finite floats in summaries are emitted as the strings `"NaN"`/`"Infinity"` (same convention as the artifacts).

- [ ] **Step 1: Write the failing tests** (module fixtures: `ids`, a namespace built by running the Task 7 and 8 chain once on the synthetic dataset with `dataset`, `split`, `binning`, `selection`, `scorecard`, `gbm`, `features` (the scorecard's features), `scores` (champion, calibrated) and `chal_scores`; `report_inputs`, an `EvaluateThresholdsInput` built by running the five validation tools once)

```python
def test_validation_chain_produces_report(call, store, ids) -> None:
    disc = ArtifactRef.model_validate(call("validation", "discrimination_metrics", DiscriminationInput(scores_id=ids.scores, model_name="m", seed=42)).structured_content)
    assert set(disc.summary["auc"]) == {"train", "val", "oot"} and 0.6 < disc.summary["auc"]["oot"] < 0.9
    cal = ArtifactRef.model_validate(call("validation", "calibration_tests", CalibrationTestsInput(scores_id=ids.scores, split="oot")).structured_content)
    assert store.get_model(cal.id, CalibrationTests).split == "oot"
    stab = ArtifactRef.model_validate(call("validation", "stability_tests", StabilityInput(scores_id=ids.scores, dataset_id=ids.dataset, binning_id=ids.binning, features=ids.features, compare_split="oot")).structured_content)
    assert [e.feature for e in store.get_model(stab.id, StabilityTests).feature_csi] == ids.features
    sens = ArtifactRef.model_validate(call("validation", "sensitivity_analysis", SensitivityInput(model_id=ids.scorecard, binning_id=ids.binning, dataset_id=ids.dataset)).structured_content)
    assert sens.summary["all_signs_ok"] is True
    bench = ArtifactRef.model_validate(call("validation", "benchmark_models", BenchmarkInput(champion_scores_id=ids.scores, challenger_scores_id=ids.chal_scores, champion="m", challenger="g", gap_threshold=0.03, seed=42)).structured_content)
    assert bench.summary["decided_on"] == "oot"
    report = ArtifactRef.model_validate(call("validation", "evaluate_thresholds", EvaluateThresholdsInput(model_name="m", thresholds=Thresholds(), discrimination_id=disc.id, calibration_id=cal.id, stability_id=stab.id, sensitivity_id=sens.id, benchmark_id=bench.id, selection_id=ids.selection, binning_id=ids.binning, oot_available=True)).structured_content)
    vr = store.get_model(report.id, ValidationReport)
    assert len(vr.tests) == 13 and report.summary["overall"] == vr.overall and report.summary["statuses"]["sens.signs"] == "pass"

def test_evaluate_without_benchmark_and_sensitivity(call, store, ids, report_inputs) -> None:
    report = ArtifactRef.model_validate(call("validation", "evaluate_thresholds", report_inputs.model_copy(update={"sensitivity_id": None, "benchmark_id": None})).structured_content)
    assert report.summary["statuses"]["bench.gap"] == "not_applicable" and report.summary["statuses"]["sens.signs"] == "not_applicable"

def test_sensitivity_rejects_gbm(call, ids) -> None:
    r = call("validation", "sensitivity_analysis", SensitivityInput(model_id=ids.gbm, binning_id=ids.binning, dataset_id=ids.dataset))
    assert r.is_error and "invalid_input" in r.content[0].text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/mcp/tests/test_validation.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `servers/validation.py` and register it**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/mcp -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/mcp
git commit -m "feat(mcp): validation server (discrimination, calibration, stability, sensitivity, benchmark, thresholds)"
```

---

### Task 10: doc-renderer server and the SR 11-7 development-document template

**Files:**
- Create: `templates/sr11-7-dev-doc/sections.yaml`, `templates/sr11-7-dev-doc/document.md.j2`, `packages/mcp/modelforge_mcp/servers/doc_renderer.py`
- Modify: `packages/mcp/modelforge_mcp/schemas.py`, `registry.py`
- Test: `packages/mcp/tests/test_doc_renderer.py`

**Interfaces:**
- Produces in `schemas.py`: `SectionSpec(id: str, title: str, required: bool, artifact_kinds: list[str], guidance: str)`; `TemplateSections(template_id: str, sections: list[SectionSpec])`; `Citation(marker: str, kind: Literal["A", "K"], target: str)`; `DocumentSection(section_id: str, body_md: str, citations: list[Citation] = [])`; `DocumentSections(template_id: str, sections: list[DocumentSection])`; `CompletenessReport(template_id: str, document_id: str, missing_sections: list[str], unresolved_markers: list[str], uncited_regulatory_sentences: list[str], complete: bool)`.
- Produces (`SERVER_NAME = "doc-renderer"`): `ListTemplateSectionsInput(template_id: str)` -> `TemplateSections`; `RenderDocumentInput(template_id: str, sections_id: str, title: str, context: dict[str, str] = {})` -> `RenderOutput(markdown: ArtifactRef, html: ArtifactRef)` (kinds `document_md`, `document_html`; summary `{"sections_rendered", "sections_missing"}`); `CheckCompletenessInput(document_id: str, template_id: str)` -> `ArtifactRef(completeness)` summary `{"complete", "missing_sections", "n_unresolved_markers", "n_uncited"}`. Helpers: `load_template(ctx, template_id) -> TemplateSections` (unknown id -> `invalid_input`), `MARKER = re.compile(r"\[(A|K):([^\]\s]+)\]")`, `REGULATIONS = ("SR 11-7", "SR 26-2", "OCC Bulletin 2026-13", "CECL", "IFRS 9", "Basel")`, `BANNER = "> **REQUIRES AUTHOR ATTENTION**: this section was not supplied."`.
- Template: `sections.yaml` is a list of eleven entries, in this order, with these ids and titles: `executive_summary` "Executive summary"; `purpose_and_use` "Model purpose and intended use"; `regulatory_context` "Regulatory and policy context"; `data` "Data"; `methodology` "Methodology and conceptual soundness"; `development` "Model development"; `performance` "Performance and validation results"; `limitations` "Limitations and weaknesses"; `monitoring` "Ongoing monitoring plan"; `governance` "Governance and lineage"; `appendices` "Appendices". `required: true` for all but `appendices`. `artifact_kinds` per section: data -> `[profile, leakage, psi, split_manifest, column_dictionary]`; methodology -> `[modeling_plan, binning_tables]`; development -> `[binning_tables, feature_selection, scorecard, gbm_model, calibration_map]`; performance -> `[validation_report, discrimination, calibration_tests, stability, sensitivity, benchmark]`; governance -> `[manifest]`; appendices -> `[binning_tables, scorecard, validation_report]`; others `[]`. Each `guidance` is one or two sentences written by the implementer from umbrella section 8.2. `document.md.j2` renders `# {{ title }}`, then for each template section `## {{ loop.index }}. {{ section.title }}` followed by the body or `BANNER`; the context values appear only in a `Model: ... Dataset: ...` line under the title; no dates.

- [ ] **Step 1: Write the failing tests**

```python
def test_list_template_sections(call) -> None:
    out = TemplateSections.model_validate(call("doc-renderer", "list_template_sections", ListTemplateSectionsInput(template_id="sr11-7-dev-doc")).structured_content)
    assert [s.id for s in out.sections][:3] == ["executive_summary", "purpose_and_use", "regulatory_context"] and len(out.sections) == 11
    assert not out.sections[-1].required and "validation_report" in next(s for s in out.sections if s.id == "performance").artifact_kinds

def test_render_is_deterministic_and_flags_missing(call, store) -> None:
    sections = DocumentSections(template_id="sr11-7-dev-doc", sections=[DocumentSection(section_id="executive_summary", body_md="AUC 0.81 [A:" + "a" * 64 + "#by_split.oot.auc]. SR 11-7 requires effective challenge [K:sr117-s4].")])
    sid = store.put_model(sections, kind="document_sections").id
    req = RenderDocumentInput(template_id="sr11-7-dev-doc", sections_id=sid, title="PD model", context={"model": "m", "dataset": "synthetic_credit"})
    a = RenderOutput.model_validate(call("doc-renderer", "render_document", req).structured_content)
    b = RenderOutput.model_validate(call("doc-renderer", "render_document", req).structured_content)
    md = store.get(a.markdown.id).decode()
    assert a.markdown.id == b.markdown.id and a.html.id == b.html.id
    assert md.startswith("# PD model") and md.count("REQUIRES AUTHOR ATTENTION") == 10 and "## 1. Executive summary" in md
    assert "<h1>" in store.get(a.html.id).decode() and a.markdown.summary == {"sections_rendered": 1, "sections_missing": 10}

def test_check_completeness(call, store) -> None:
    body = "Fine [A:" + "b" * 64 + "]. Bad marker [A:not-a-hash]. CECL requires lifetime losses. IFRS 9 is cited [K:ifrs9-1]."
    sid = store.put_model(DocumentSections(template_id="sr11-7-dev-doc", sections=[DocumentSection(section_id=s, body_md=body) for s in ("executive_summary", "data")]), kind="document_sections").id
    doc = RenderOutput.model_validate(call("doc-renderer", "render_document", RenderDocumentInput(template_id="sr11-7-dev-doc", sections_id=sid, title="t")).structured_content)
    ref = ArtifactRef.model_validate(call("doc-renderer", "check_completeness", CheckCompletenessInput(document_id=doc.markdown.id, template_id="sr11-7-dev-doc")).structured_content)
    report = store.get_model(ref.id, CompletenessReport)
    assert report.complete is False and "purpose_and_use" in report.missing_sections and "appendices" not in report.missing_sections
    assert report.unresolved_markers == ["[A:not-a-hash]"] * 2 and len(report.uncited_regulatory_sentences) == 2

def test_unknown_template_is_invalid_input(call) -> None:
    assert call("doc-renderer", "list_template_sections", ListTemplateSectionsInput(template_id="nope")).is_error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/mcp/tests/test_doc_renderer.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write the template files and implement `servers/doc_renderer.py`; register it**

Missing-section detection for `check_completeness` reads the rendered Markdown: a required section is missing when its `## n. Title` heading is followed by `BANNER` before the next heading. Jinja2 `Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)`; HTML via `MarkdownIt("commonmark").render(md)` inside a fixed skeleton with a small embedded stylesheet.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/mcp -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/mcp templates
git commit -m "feat(mcp): doc-renderer server and SR 11-7 style development-document template"
```

---

### Task 11: Tool contract snapshots and golden hashes

**Files:**
- Create: `packages/mcp/modelforge_mcp/contracts.py`, `packages/mcp/contracts/` (committed snapshots and goldens)
- Modify: `packages/mcp/modelforge_mcp/__main__.py`, `Makefile`
- Test: `packages/mcp/tests/test_contracts.py`

**Interfaces:**
- Produces: `contracts.CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"`; `platform_key() -> str` (`f"{sys.platform}-{platform.machine()}"`); `schema_snapshot(spec: ToolSpec) -> dict[str, Any]` (`{"server", "tool", "version", "input_schema": spec.input_model.model_json_schema(), "output_schema": spec.output_model.model_json_schema()}`); `snapshot_path(spec) -> Path` (`CONTRACTS_DIR / server / f"{tool}@{version}.json"`); `write_snapshots() -> list[Path]`; `run_golden_chain(settings: ServerSettings) -> dict[tuple[ServerName, str], list[ArtifactRef]]` (calls every artifact-producing tool in dependency order on `DatasetRef(adapter="synthetic_credit", seed=42)` with the Task 8 and 9 fixture arguments, plus a fixed two-section `DocumentSections` for the renderer; `list_template_sections` excluded); `golden_path(spec, platform: str) -> Path` (`CONTRACTS_DIR / "goldens" / platform / server / f"{tool}@{version}.sha256"`, content = newline-joined artifact ids); `write_goldens(settings) -> list[Path]`. `__main__` gains `contracts [--out DIR]` (writes snapshots; default `CONTRACTS_DIR`) and `goldens [--artifact-dir DIR]` (writes goldens for this platform). Makefile `goldens: uv run modelforge-mcp goldens`.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_tool_has_a_committed_schema_snapshot_that_matches() -> None:
    for spec in TOOLS.values():
        path = snapshot_path(spec)
        assert path.exists(), f"missing snapshot {path}; run `uv run modelforge-mcp contracts` and bump the version if the schema changed"
        assert json.loads(path.read_text()) == schema_snapshot(spec)

def test_no_orphan_snapshots() -> None:
    expected = {snapshot_path(s) for s in TOOLS.values()}
    actual = {p for p in CONTRACTS_DIR.glob("*/*.json")}
    assert actual == expected

def test_golden_hashes_match_for_this_platform(settings) -> None:
    key = platform_key()
    if not (CONTRACTS_DIR / "goldens" / key).exists():
        pytest.skip(f"no goldens committed for {key}; run `make goldens` and commit packages/mcp/contracts/goldens/{key}")
    produced = run_golden_chain(settings)
    for (server, tool), refs in produced.items():
        spec = TOOLS[(server, tool)]
        assert golden_path(spec, key).read_text().split() == [r.id for r in refs], f"{server}/{tool}@{spec.version} output changed without a version bump"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/mcp/tests/test_contracts.py -v`
Expected: FAIL (`ImportError`, then missing snapshots).

- [ ] **Step 3: Implement `contracts.py`, extend `__main__`, add the Makefile target; generate and commit the snapshots and this platform's goldens**

Run: `uv run modelforge-mcp contracts && make goldens`
Expected: 20 snapshot files under `packages/mcp/contracts/<server>/`, 19 golden files under `packages/mcp/contracts/goldens/<platform>/` (every tool except `list_template_sections`).

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/mcp -v && make lint`
Expected: all pass; running `make goldens` a second time changes no file (`git status --short packages/mcp/contracts` is empty).

- [ ] **Step 5: Commit**

```bash
git add packages/mcp Makefile
git commit -m "test(mcp): tool contract snapshots and per-platform golden output hashes"
```

---
### Task 12: Orchestrator configuration, brief, state schemas, scope mapping

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/config.py`, `briefs.py`, `schemas.py`, `examples/briefs/polish-pd.yaml`, `examples/briefs/synthetic.yaml`
- Test: `packages/orchestrator/tests/conftest.py`, `test_config.py`, `test_briefs.py`, `test_schemas.py`

**Interfaces:**
- Consumes: core `ModelingPlan`, `TargetDefinition`, `VintagePolicy`, `FeaturePolicy`, `Methodology`, `Thresholds`, `DEFAULT_THRESHOLDS`; lineage `ArtifactRef`, `GateName`, `GateDecision`, `RunStatus`; mcp `DatasetRef`.
- Produces:
  - `config.Settings` (frozen dataclass): `database_url: str`, `artifact_dir: Path`, `data_dir: Path`, `templates_dir: Path`, `profile: str`, `mcp_command: tuple[str, ...] | None`, `tool_timeout_s: float`, `log_level: str`; `load_settings(env: Mapping[str, str] | None = None, *, dotenv: Path | None = Path(".env")) -> Settings` (environment names and defaults from spec 5.10; `.env` lines `KEY=VALUE` are read when the file exists and never override real environment variables; `MODELFORGE_MCP_COMMAND` is split with `shlex`); `server_settings(settings) -> ServerSettings`.
  - `briefs.Brief(StrictModel)`: `model_name: str`, `portfolio: str`, `dataset: DatasetRef`, `target: TargetDefinition`, `intended_use: str`, `regulatory_scope: list[str]`, `tier: Literal[1, 2, 3]`, `vintage_policy: VintagePolicy`, `feature_policy: FeaturePolicy = FeaturePolicy()`, `thresholds: dict[str, float] = {}`, `constraints: str = ""`; `class BriefError(ValueError)`; `load_brief(path: Path) -> tuple[Brief, bytes]` (YAML errors and validation errors raise `BriefError` with the message); `parse_brief(data: bytes) -> Brief`; `brief_hash(data: bytes) -> str`; `plan_from_brief(brief: Brief) -> ModelingPlan` (`methodology=Methodology()`, `thresholds=DEFAULT_THRESHOLDS.model_copy(update=brief.thresholds)`, other fields copied).
  - `schemas.py`: `RefName = Literal["dataset", "dictionary", "profile", "leakage", "psi", "split", "binning", "selection", "champion", "challenger_model", "calibration", "champion_scores", "challenger_scores", "discrimination", "calibration_tests", "stability", "sensitivity", "benchmark", "validation", "document_sections", "document_md", "document_html", "completeness", "findings", "responses", "manifest", "replay_report"]`; `DETERMINISTIC_REFS: tuple[RefName, ...]` = the replay comparison keys of spec 5.7; `Counters(build_loops: int = 0, remediation_rounds: int = 0)`; `ErrorRecord(node: str, attempt: int, kind: Literal["tool", "validation", "internal"], error: dict[str, Any], ts: str)`; `GatePrompt(gate: GateName, run_id: str, summary: dict[str, Any])`; `merge_dicts(a, b) -> dict`; `RunState(StrictModel)` exactly as spec 5.1 (`refs: Annotated[dict[RefName, ArtifactRef], merge_dicts]`, `gates: Annotated[dict[GateName, GateDecision], merge_dicts]`, `errors: Annotated[list[ErrorRecord], operator.add]`); `ApprovalSummary(gate: GateName, decision: str, approver: str, ts: str)`; `Manifest(StrictModel)` as spec 5.6 including `initial_plan_ref`; `ReplayMismatch(ref: RefName, original_id: str, replay_id: str)`; `ReplayReport(original_run_id: str, replay_run_id: str, matched: list[RefName], mismatched: list[ReplayMismatch], missing: list[RefName], ok: bool)`; `Finding(id: str, round: int, severity: Literal["critical", "high", "medium", "low"], category: Literal["conceptual_soundness", "data", "methodology", "performance", "documentation", "governance", "limitations", "monitoring"], title: str, description: str, evidence: list[str], required_action: str, status: Literal["open", "accepted", "disputed", "closed"] = "open", response: str | None = None, response_evidence: list[str] = [])`; `FindingsList(round: int, findings: list[Finding])`; `FindingResponse(finding_id: str, decision: Literal["accept", "dispute"], action: str, evidence: list[str] = [])`; `ResponsesList(round: int, responses: list[FindingResponse])`.
  - `examples/briefs/polish-pd.yaml` with the spec 5.11 content; `examples/briefs/synthetic.yaml` with `model_name: synthetic_pd`, `dataset: {adapter: synthetic_credit, seed: 42}`, `vintage_policy: {oot_vintages: [5]}`, `tier: 3`, the same policy defaults.

- [ ] **Step 1: Write `conftest.py` and the failing tests**

`conftest.py`: `pytest_plugins = ["modelforge_lineage.testing"]`; fixture `settings(tmp_path, database_url)` returning `Settings` with `artifact_dir=tmp_path / "artifacts"`, `data_dir` = the repo `data/` directory (or `MODELFORGE_DATA_DIR`), `templates_dir` = repo `templates/`, and the temporary database URL from the `database` fixture; fixture `checkpointer(database)` that calls `PostgresSaver(database.pool).setup()` once per session; `REPO = Path(__file__).resolve().parents[3]`.

```python
# test_config.py
def test_defaults_and_env_and_dotenv(tmp_path: Path) -> None:
    s = load_settings({}, dotenv=None)
    assert s.database_url == "postgresql://modelforge:modelforge@localhost:5432/modelforge" and s.tool_timeout_s == 600 and s.mcp_command is None
    (tmp_path / ".env").write_text("MODELFORGE_PROFILE=ci-cheap\nMODELFORGE_MCP_COMMAND=uv run modelforge-mcp\n")
    s = load_settings({"MODELFORGE_PROFILE": "default"}, dotenv=tmp_path / ".env")
    assert s.profile == "default" and s.mcp_command == ("uv", "run", "modelforge-mcp")

# test_briefs.py
def test_example_briefs_load_and_map_to_plans() -> None:
    brief, data = load_brief(REPO / "examples/briefs/polish-pd.yaml")
    assert brief.dataset == DatasetRef(adapter="polish_bankruptcy", seed=42) and brief_hash(data) == hashlib.sha256(data).hexdigest()
    plan = plan_from_brief(brief)
    assert plan.model_name == "polish_bankruptcy_pd" and plan.thresholds == DEFAULT_THRESHOLDS and plan.vintage_policy.oot_vintages == [5]
    assert load_brief(REPO / "examples/briefs/synthetic.yaml")[0].dataset.adapter == "synthetic_credit"

def test_threshold_overrides_and_errors(tmp_path: Path) -> None:
    text = (REPO / "examples/briefs/synthetic.yaml").read_text().replace("thresholds: {}", "thresholds: {auc_min: 0.75}")
    assert plan_from_brief(parse_brief(text.encode())).thresholds.auc_min == 0.75
    with pytest.raises(BriefError):
        parse_brief(b"model_name: x\n")
    with pytest.raises(BriefError):
        parse_brief(b": not yaml :")

# test_schemas.py
def test_run_state_round_trips_and_merges() -> None:
    state = RunState(run_id="run_a", requester="alice", seed=42, brief_hash="h", brief_ref="b" * 64, dataset_ref=DatasetRef(adapter="synthetic_credit"))
    assert RunState.model_validate_json(state.model_dump_json()) == state
    ref = ArtifactRef(id="a" * 64, kind="profile", media_type="application/json", size_bytes=1)
    assert merge_dicts({"profile": ref}, {"split": ref}) == {"profile": ref, "split": ref}
    assert set(DETERMINISTIC_REFS) < set(get_args(RefName)) and "document_sections" not in DETERMINISTIC_REFS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the three modules and write the two briefs**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/orchestrator -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator examples/briefs
git commit -m "feat(orchestrator): settings, modeling brief, run state and manifest schemas, example briefs"
```

---

### Task 13: MCP tool client (stdio bridge, in-process variant, typed errors, retries)

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/tools.py`, `deps.py`
- Test: `packages/orchestrator/tests/test_tools.py`

**Interfaces:**
- Consumes: `mcp.Client`, `StdioServerParameters`, `CallToolResult`; `TOOLS`, `build_server`, `ServerName`, `ToolErrorPayload` (mcp package); `canonical_json`, `sha256_hex`, `ArtifactRef`, `ArtifactStore`, `Lineage`; `Settings`, `server_settings`.
- Produces:
  - `class ToolError(Exception)`: keyword-only constructor and attributes `server: str`, `tool: str`, `version: str`, `code: str` (`ToolErrorCode | Literal["transport", "timeout"]`), `message: str`; `to_dict() -> dict[str, str]`; `str(err)` is `f"{server}/{tool}@{version} {code}: {message}"`.
  - `@dataclass(frozen=True) class ToolCall(Generic[T])`: `server: ServerName`, `tool: str`, `version: str`, `input_hash: str`, `output: T`, `refs: list[ArtifactRef]`, `duration_ms: int`.
  - `class ToolClient(Protocol)`: `def call(self, server: ServerName, tool: str, request: StrictModel, *, timeout_s: float | None = None) -> ToolCall[Any]`; `__enter__`/`__exit__`.
  - `extract_refs(output: StrictModel) -> list[ArtifactRef]` (the output itself if it is an `ArtifactRef`, else every top-level field that is an `ArtifactRef` or a list of them, in declaration order).
  - `parse_error(text: str) -> tuple[str, str]` (finds the JSON object in a `ToolError` text and returns `(code, message)`; a validation-error text without JSON returns `("invalid_input", text)`; anything else `("internal", text)`).
  - `class _Bridge`: owns one thread and event loop; `__init__(self, factories: Mapping[ServerName, Callable[[], AbstractAsyncContextManager[Client]]])`; `start(timeout_s: float = 60)` runs a single owning coroutine that enters every client context in an `AsyncExitStack`, records `versions[server][tool]` from `list_tools`, signals ready, then waits for stop and exits the stack in that same task; `call_tool(server, tool, args: dict, *, timeout_s: float) -> CallToolResult` via `run_coroutine_threadsafe`; `stop()`.
  - `class BaseToolClient(ToolClient)`: `__init__(self, bridge_factories, *, default_timeout_s: float, backoff_s: Sequence[float] = (1.0, 2.0, 4.0))`; `call` hashes the request, measures duration, retries only `transport`/`timeout` errors over `backoff_s` (so `len(backoff_s) + 1` attempts), validates `structured_content` into `TOOLS[(server, tool)].output_model`, raises `ToolError` with `parse_error` on `is_error`, maps `concurrent.futures.TimeoutError` to `timeout` and any other exception from the bridge to `transport`.
  - `class StdioToolClient(BaseToolClient)`: `__init__(self, settings: Settings, *, backoff_s=...)`; command = `settings.mcp_command or (sys.executable, "-m", "modelforge_mcp")` plus the server name; `env` = the current environment plus `MODELFORGE_ARTIFACT_DIR`, `MODELFORGE_DATA_DIR`, `MODELFORGE_TEMPLATES_DIR` from settings.
  - `class InProcessToolClient(BaseToolClient)`: `__init__(self, settings: Settings, *, servers: Iterable[ServerName] = all four, backoff_s=...)`, factories build `Client(build_server(name, server_settings(settings)))`.
  - `deps.Deps` (dataclass): `tools: ToolClient`, `lineage: Lineage`, `store: ArtifactStore`, `settings: Settings`.

- [ ] **Step 1: Write the failing tests**

```python
def test_in_process_call_returns_typed_output_and_refs(settings) -> None:
    with InProcessToolClient(settings, servers=["data-profiler"]) as client:
        call = client.call("data-profiler", "load_dataset", LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit")))
    assert isinstance(call.output, LoadDatasetOutput) and [r.kind for r in call.refs] == ["dataset", "column_dictionary"]
    assert call.version == "1.0.0" and call.input_hash == sha256_hex(canonical_json({"dataset_ref": {"adapter": "synthetic_credit", "seed": 42}})) and call.duration_ms >= 0

def test_typed_error_is_not_retried(settings) -> None:
    with InProcessToolClient(settings, servers=["data-profiler"], backoff_s=(0.01, 0.01)) as client:
        t0 = time.monotonic()
        with pytest.raises(ToolError) as exc:
            client.call("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id="0" * 64))
    assert exc.value.code == "artifact_not_found" and exc.value.server == "data-profiler" and time.monotonic() - t0 < 1.0

def test_stdio_client_calls_real_subprocess(settings) -> None:
    with StdioToolClient(settings) as client:
        call = client.call("data-profiler", "load_dataset", LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit")))
        again = client.call("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id=call.output.dataset.id))
    assert isinstance(call.output, LoadDatasetOutput) and again.refs[0].kind == "profile"

def test_dead_server_is_transport_error_and_no_orphans(settings) -> None:
    dead = dataclasses.replace(settings, mcp_command=(sys.executable, "-c", "import sys; sys.exit(3)"))   # dies before the handshake
    with pytest.raises(ToolError) as exc, StdioToolClient(dead, backoff_s=(0.01,)) as client:
        client.call("data-profiler", "load_dataset", LoadDatasetInput(dataset_ref=DatasetRef(adapter="synthetic_credit")))
    assert exc.value.code == "transport"
    assert not any("modelforge_mcp" in line for line in subprocess.run(["pgrep", "-fl", "modelforge_mcp"], capture_output=True, text=True).stdout.splitlines())

def test_parse_error_and_extract_refs() -> None:
    assert parse_error('Error executing tool x: {"code": "invalid_input", "message": "bad"}') == ("invalid_input", "bad")
    assert parse_error("Error executing tool x: 1 validation error for xArguments")[0] == "invalid_input"
    assert parse_error("boom")[0] == "internal"
    ref = ArtifactRef(id="a" * 64, kind="scorecard", media_type="application/json", size_bytes=1)
    assert extract_refs(FitLogisticOutput(scorecard=ref, selection=ref)) == [ref, ref] and extract_refs(ref) == [ref]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator/tests/test_tools.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `tools.py` and `deps.py`**

Bridge start failures (a server that exits before the handshake) surface as the owning coroutine's exception; `start` re-raises it in the calling thread and `__enter__` fails cleanly after joining the thread, so `__exit__` never runs against a half-open bridge. Timeouts use `read_timeout_seconds=timeout_s` on `call_tool` and `fut.result(timeout=timeout_s + 5)`.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/orchestrator/tests/test_tools.py -v && make lint`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator
git commit -m "feat(orchestrator): MCP tool client over stdio with an in-process variant, typed errors and bounded retries"
```

---

### Task 14: Step recorder and the intake, scope, profile and split nodes

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/steps.py`, `nodes/__init__.py`, `nodes/intake.py`, `nodes/scope.py`, `nodes/profile.py`, `nodes/split.py`
- Test: `packages/orchestrator/tests/test_steps.py`, `test_nodes_early.py`

**Interfaces:**
- Consumes: Tasks 3 to 6 (lineage repositories), Task 13 (`ToolClient`, `ToolCall`, `ToolError`, `Deps`), Task 12 schemas, `langgraph.runtime.Runtime`.
- Produces:
  - `steps.state_hash(state: RunState) -> str` (`sha256_hex(canonical_json(state.model_dump(mode="json")))`).
  - `class Step`: `__init__(self, deps: Deps, state: RunState, node: str)`; `__enter__` inserts the step row (`start_step`) and appends `step.started {"node", "attempt", "input_state_hash"}`; `record: StepRecord` attribute; `tool(self, server, tool, request, *, timeout_s=None) -> ToolCall` (calls `deps.tools.call`; in one transaction inserts the `ToolCallRecord` (`output_hash` = the first ref's id, or the sha256 of the canonical output JSON when there is none), one `artifacts` row per ref, appends `tool.called {"server", "tool", "version", "input_hash", "output_hash", "duration_ms", "artifact_ids"}` and `artifact.stored {"id", "kind"}` per ref; on `ToolError` appends `tool.failed {"server", "tool", "code", "message"}` and re-raises); `store(self, model: BaseModel, *, kind: str, summary: dict | None = None) -> ArtifactRef` and `store_bytes(self, data: bytes, *, kind, media_type, summary=None) -> ArtifactRef` (put, insert artifact row with this step as producer, `artifact.stored`); `event(self, type: str, payload: dict) -> None`; `complete(self, update: dict[str, Any]) -> dict[str, Any]` (computes `output_state_hash` of `state.model_copy(update=...)` after applying the same reducers, stores it, returns `update` unchanged); `__exit__` finishes the step `succeeded` (with the recorded output hash, or `None` when `complete` was not called) and appends `step.finished {"status", "output_state_hash"}`; on exception finishes `failed`, appends `error.recorded` with an `ErrorRecord` (`kind="tool"` for `ToolError`, `"validation"` for `pydantic.ValidationError`/`BriefError`, else `"internal"`; `error` = `ToolError.to_dict()` or `{"type", "message"}`), and re-raises. `Step.last_error: ErrorRecord | None` is set in that case.
  - Node signatures, all `(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]`:
    - `intake.intake`: loads the brief bytes from `state.brief_ref`, `parse_brief`; when `state.replay_of is None` calls `inventory.create_stub(name=brief.model_name, owner=state.requester, intended_use=brief.intended_use, tier=brief.tier)`; returns `{}` through `step.complete`.
    - `scope.scope`: normal mode stores `plan_from_brief(brief)` as `modeling_plan` and calls `runs.set_plan_ref`; replay mode loads the original run's manifest (`runs.get_run(replay_of).manifest_hash` -> `Manifest`) and its `initial_plan_ref` plan, stores it (same id) and sets `plan_ref`; returns `{"plan": plan}`.
    - `profile.profile`: `load_dataset` (refs `dataset`, `dictionary`; `runs.set_dataset_id`), `profile_dataset`, `check_leakage`, and `compute_psi` only when `plan.vintage_policy.oot_vintages` is non-empty and the dataset is OOT-capable (`base_vintages` = sorted vintages present in the profile's `rows_by_vintage` minus the OOT ones); returns `{"refs": {...}}`.
    - `split.split`: `strategy = "vintage"` when OOT-capable and OOT vintages are listed, else `"stratified_random"` with `oot_vintages=[]`; `seed=state.seed`, `val_share=0.2`; returns `{"refs": {"split": ref}}`.
  - `nodes/__init__.py` exports `brief_of(state, store) -> Brief` and `plan_of(state) -> ModelingPlan` (raises `RuntimeError` when `state.plan is None`).

- [ ] **Step 1: Write the failing tests**

Test helpers in `conftest.py`: `make_run(db, store, brief_path, *, requester="alice", replay_of=None) -> RunState` (stores the brief bytes as a `brief` artifact, `create_run` with `run_id=new_id("run")`, returns the initial state); `deps(settings, database, store)` fixture yielding `Deps` with an `InProcessToolClient` entered for the test; `runtime(deps)` returning a `Runtime` stub exposing `.context` (`SimpleNamespace(context=deps)` is enough for direct node calls); `synthetic_state(db, store)` = `make_run` on `examples/briefs/synthetic.yaml`; `state_no_oot(db, store, tmp_path)` = the same brief with `oot_vintages: []` written to `tmp_path`. Later tasks add `scoped_state` (after `intake` and `scope`), `prepared_state` (after `split`), `prepared_state_no_oot`, `validated_state` (after `validate`) and `signed_off_state` (after `remediate`, with `gates["signoff"]` = `approve_with_conditions` and `status="completed"`), each built by calling the earlier nodes directly.

```python
# test_steps.py
@pytest.mark.db
def test_step_records_tool_call_artifacts_and_events(deps, runtime, synthetic_state) -> None:
    with Step(deps, synthetic_state, "profile") as step:
        call = step.tool("data-profiler", "load_dataset", LoadDatasetInput(dataset_ref=synthetic_state.dataset_ref))
        update = step.complete({"refs": {"dataset": call.refs[0]}})
    with deps.lineage.connection() as conn:
        steps_ = list_steps(conn, synthetic_state.run_id); calls = list_tool_calls(conn, synthetic_state.run_id)
        types = [e.type for e in list_events(conn, synthetic_state.run_id)]; arts = list_artifacts(conn, synthetic_state.run_id)
    assert steps_[0].status == "succeeded" and steps_[0].output_state_hash == state_hash(synthetic_state.model_copy(update={"refs": update["refs"]}))
    assert calls[0].artifact_ids == [r.id for r in call.refs] and {a.kind for a in arts} == {"dataset", "column_dictionary"}
    assert types[-4:] == ["tool.called", "artifact.stored", "artifact.stored", "step.finished"] and types[-5] == "step.started"

@pytest.mark.db
def test_step_failure_is_recorded_and_reraised(deps, runtime, synthetic_state) -> None:
    with pytest.raises(ToolError):
        with Step(deps, synthetic_state, "profile") as step:
            step.tool("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id="0" * 64))
    with deps.lineage.connection() as conn:
        events = list_events(conn, synthetic_state.run_id); steps_ = list_steps(conn, synthetic_state.run_id)
    assert steps_[0].status == "failed" and [e.type for e in events][-3:] == ["tool.failed", "error.recorded", "step.finished"]
    assert events[-2].payload["kind"] == "tool" and events[-2].payload["error"]["code"] == "artifact_not_found"
```

```python
# test_nodes_early.py (all @pytest.mark.db)
def test_intake_scope_profile_split_on_synthetic(deps, runtime, synthetic_state) -> None:
    state = synthetic_state
    intake(state, runtime)
    with deps.lineage.connection() as conn:
        assert get_for_run(conn, state.run_id).status == "draft"
    state = state.model_copy(update=scope(state, runtime))
    assert state.plan is not None and state.plan.model_name == "synthetic_pd"
    with deps.lineage.connection() as conn:
        assert get_run(conn, state.run_id).plan_ref is not None
    state = state.model_copy(update={"refs": profile(state, runtime)["refs"]})
    assert {"dataset", "dictionary", "profile", "leakage", "psi"} <= set(state.refs) and state.refs["psi"].summary["max_psi"] < 0.1
    with deps.lineage.connection() as conn:
        assert get_run(conn, state.run_id).dataset_id == state.refs["dataset"].id
    state = state.model_copy(update={"refs": {**state.refs, **split(state, runtime)["refs"]}})
    assert state.refs["split"].summary["oot_available"] is True
    with deps.lineage.connection() as conn:
        assert [s.node for s in list_steps(conn, state.run_id)] == ["intake", "scope", "profile", "split"]

def test_profile_skips_psi_and_split_is_random_without_oot(deps, runtime, state_no_oot) -> None:   # brief with oot_vintages: []
    state = state_no_oot.model_copy(update=scope(state_no_oot, runtime))
    refs = profile(state, runtime)["refs"]
    assert "psi" not in refs
    state = state.model_copy(update={"refs": refs})
    assert split(state, runtime)["refs"]["split"].summary["oot_available"] is False

def test_intake_rejects_malformed_brief(deps, runtime, db, store) -> None:
    state = make_run(db, store, brief_bytes=b"model_name: only\n")
    with pytest.raises(BriefError):
        intake(state, runtime)
    with deps.lineage.connection() as conn:
        assert list_steps(conn, state.run_id)[0].status == "failed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator/tests/test_steps.py packages/orchestrator/tests/test_nodes_early.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `steps.py` and the four nodes**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/orchestrator -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator
git commit -m "feat(orchestrator): step recorder; intake, scope stub, profile and split nodes"
```

---

### Task 15: features, build and validate nodes

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/nodes/features.py`, `nodes/build.py`, `nodes/validate.py`
- Test: `packages/orchestrator/tests/test_nodes_model.py`

**Interfaces:**
- Consumes: Task 14 (`Step`, `plan_of`), Task 8 and 9 input models.
- Produces (`(state, runtime) -> dict[str, Any]`):
  - `features.features`: candidates = `ColumnDictionary.feature_names` of `refs["dictionary"]` minus `refs["leakage"].summary["flagged"]` minus `plan.feature_policy.exclusions`; `bin_features_woe(dataset_id, split_id, features=candidates)`; `select_features(iv_min, iv_max, corr_max, max_features` from `plan.feature_policy)`; refs `binning`, `selection`.
  - `build.build`: `fit_logistic_pd(model_name=plan.model_name, stepwise=True)` -> refs `champion`, `selection` (overwritten by the merged selection); `fit_gbm_challenger(features=champion.summary["features"], model_name=f"{plan.model_name}_gbm", seed=state.seed)` -> `challenger_model`; `calibrate(model_id=champion, fit_split="val", method="platt")` -> `calibration`; `score(champion, calibration_id)` -> `champion_scores`; `score(challenger_model)` -> `challenger_scores`.
  - `validate.validate`: `oot_available = refs["split"].summary["oot_available"]`; `eval_split = "oot" if oot_available else "val"`; `discrimination_metrics(model_name=plan.model_name, seed=state.seed)`; `calibration_tests(split=eval_split, alpha=plan.thresholds.calibration_alpha)`; `stability_tests(features=champion.summary["features"], compare_split=eval_split)`; `sensitivity_analysis(model_id=champion)`; `benchmark_models(champion=plan.model_name, challenger=f"{plan.model_name}_gbm", gap_threshold=plan.thresholds.benchmark_gap, seed=state.seed)`; `evaluate_thresholds(thresholds=plan.thresholds, oot_available=...)`; refs `discrimination`, `calibration_tests`, `stability`, `sensitivity`, `benchmark`, `validation`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_features_build_validate_follow_the_example(deps, runtime, prepared_state) -> None:   # fixture: synthetic state after intake..split
    state = prepared_state.model_copy(update={"refs": {**prepared_state.refs, **features(prepared_state, runtime)["refs"]}})
    assert set(state.refs["selection"].summary["selected"]) >= {"f_1", "f_2", "f_3"}
    state = state.model_copy(update={"refs": {**state.refs, **build(state, runtime)["refs"]}})
    assert state.refs["champion"].summary["fit_method"] == "stepwise_wrong_sign" and state.refs["selection"].summary["selected"] == state.refs["champion"].summary["features"]
    assert state.refs["challenger_model"].summary["features"] == state.refs["champion"].summary["features"]
    state = state.model_copy(update={"refs": {**state.refs, **validate(state, runtime)["refs"]}})
    report = deps.store.get_model(state.refs["validation"].id, ValidationReport)
    assert len(report.tests) == 13 and report.get("disc.auc.oot").status in ("pass", "warn") and report.overall in ("pass", "warn")
    with deps.lineage.connection() as conn:
        calls = list_tool_calls(conn, state.run_id)
    assert [c.tool for c in calls if c.server == "credit-modeling"] == ["bin_features_woe", "select_features", "fit_logistic_pd", "fit_gbm_challenger", "calibrate", "score", "score"]
    assert [c.tool for c in calls if c.server == "validation"] == ["discrimination_metrics", "calibration_tests", "stability_tests", "sensitivity_analysis", "benchmark_models", "evaluate_thresholds"]

@pytest.mark.db
def test_exclusions_and_leakage_flags_are_dropped_from_candidates(deps, runtime, prepared_state) -> None:
    plan = prepared_state.plan.model_copy(update={"feature_policy": prepared_state.plan.feature_policy.model_copy(update={"exclusions": ["f_6"]})})
    refs = features(prepared_state.model_copy(update={"plan": plan}), runtime)["refs"]
    tables = deps.store.get_model(refs["binning"].id, BinningTables)
    assert all(f.feature != "f_6" for f in tables.features) and refs["binning"].summary["n_features"] == 5

@pytest.mark.db
def test_validate_without_oot_uses_val(deps, runtime, prepared_state_no_oot) -> None:
    state = prepared_state_no_oot
    for node in (features, build, validate):
        state = state.model_copy(update={"refs": {**state.refs, **node(state, runtime)["refs"]}})
    assert deps.store.get_model(state.refs["calibration_tests"].id, CalibrationTests).split == "val"
    assert state.refs["validation"].summary["statuses"]["disc.auc.oot"] == "not_applicable"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator/tests/test_nodes_model.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the three nodes**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/orchestrator -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator
git commit -m "feat(orchestrator): features, build and validate nodes reproducing the core example sequence"
```

---

### Task 16: Gate nodes and the graph

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/nodes/gates.py`, `graph.py`
- Test: `packages/orchestrator/tests/test_gates.py`, `test_graph.py`

**Interfaces:**
- Consumes: `interrupt`, `Command`, `StateGraph`, `START`, `END`, `PostgresSaver`; `GateDecision`, `GatePrompt`, `list_approvals`; Tasks 14 and 15 nodes; Task 17 nodes are referenced by name only and wired in Task 17.
- Produces:
  - `gates.gate_summary(gate: GateName, state: RunState, store: ArtifactStore) -> dict[str, Any]`: `scope` -> `{"plan": plan json}`; `model` -> `{"validation": refs["validation"].summary, "selected": refs["selection"].summary["selected"], "build_loops": counters.build_loops}`; `signoff` -> `{"completeness": refs["completeness"].summary, "findings": refs["findings"].summary, "validation_overall": refs["validation"].summary["overall"]}`.
  - `gates.decide(gate: GateName, state: RunState, deps: Deps) -> GateDecision`: replay mode returns the n-th original approval for the gate (n = number of this gate's prior visits: `counters.build_loops` for `model`, 0 otherwise); otherwise `GateDecision.model_validate(interrupt(GatePrompt(gate=..., run_id=..., summary=gate_summary(...)).model_dump(mode="json")))`. Nothing is written to lineage before `interrupt` (the node body re-runs on resume; the worker appends `gate.requested`).
  - `gates.gate_scope(state, runtime) -> Command[Literal["profile", "seal"]]`: after `decide`, inside a `Step(..., "gate_scope")`, appends `gate.applied {"gate", "decision", "approver"}`; on `edit` stores the new plan as `modeling_plan`, calls `set_plan_ref`, and updates `plan`; `goto="seal"` on `reject` with `update={"status": "rejected"}`, else `goto="profile"`; `update["gates"] = {"scope": decision}`.
  - `gates.gate_model(state, runtime) -> Command[Literal["document", "features", "build", "seal"]]`: `accept` -> `document`; `adjust` -> `adjust_target`, plan replaced and stored, `counters.build_loops + 1`; `reject` -> `seal` with `status="rejected"`.
  - `gates.gate_signoff(state, runtime) -> Command[Literal["seal"]]`: records the decision in `gates["signoff"]`; `status` becomes `"completed"` for `approve`/`approve_with_conditions`, `"rejected"` for `reject`.
  - `graph.NODES: dict[str, Callable]` and `graph.build_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph`: `StateGraph(RunState, context_schema=Deps)`; nodes `intake, scope, gate_scope, profile, split, features, build, validate, gate_model, document, challenge, remediate, gate_signoff, seal`; fixed edges `START->intake->scope->gate_scope`, `profile->split->features->build->validate->gate_model`, `document->challenge->remediate->gate_signoff->seal->END`; the gate nodes route through `Command`. Until Task 17 lands, `document`, `challenge`, `remediate` and `seal` are imported from `nodes` as placeholders that return `{}` (`seal` returns `{"status": state.status}`), each marked `# replaced in Task 17`.

- [ ] **Step 1: Write the failing tests**

```python
# test_gates.py (@pytest.mark.db)
def test_gate_scope_edit_stores_plan_and_routes(deps, runtime, scoped_state, monkeypatch) -> None:    # scoped_state: after intake+scope
    edited = scoped_state.plan.model_copy(update={"feature_policy": scoped_state.plan.feature_policy.model_copy(update={"max_features": 5})})
    monkeypatch.setattr(gates, "interrupt", lambda value: GateDecision(gate="scope", decision="edit", approver="alice", plan=edited).model_dump(mode="json"))
    cmd = gate_scope(scoped_state, runtime)
    assert cmd.goto == "profile" and cmd.update["plan"].feature_policy.max_features == 5 and cmd.update["gates"]["scope"].decision == "edit"
    with deps.lineage.connection() as conn:
        run = get_run(conn, scoped_state.run_id); assert run.plan_ref == deps.store.put_model(edited, kind="modeling_plan").id
        assert [e.type for e in list_events(conn, scoped_state.run_id)][-4:] == ["step.started", "gate.applied", "artifact.stored", "step.finished"]

def test_gate_model_adjust_and_reject(deps, runtime, validated_state, monkeypatch) -> None:
    monkeypatch.setattr(gates, "interrupt", lambda value: GateDecision(gate="model", decision="adjust", approver="bob", plan=validated_state.plan, adjust_target="build").model_dump(mode="json"))
    cmd = gate_model(validated_state, runtime)
    assert cmd.goto == "build" and cmd.update["counters"].build_loops == 1
    monkeypatch.setattr(gates, "interrupt", lambda value: GateDecision(gate="model", decision="reject", approver="bob").model_dump(mode="json"))
    cmd = gate_model(validated_state, runtime)
    assert cmd.goto == "seal" and cmd.update["status"] == "rejected"

def test_gate_summary_contents(deps, validated_state) -> None:
    s = gate_summary("model", validated_state, deps.store)
    assert set(s) == {"validation", "selected", "build_loops"} and "overall" in s["validation"]

def test_replay_mode_uses_recorded_approvals_without_interrupt(deps, runtime, scoped_state, monkeypatch) -> None:
    monkeypatch.setattr(gates, "interrupt", lambda value: pytest.fail("interrupt must not be called in replay mode"))
    original = scoped_state.run_id
    with deps.lineage.transaction() as conn:
        set_status(conn, original, "awaiting_scope")
        record_approval(conn, original, GateDecision(gate="scope", decision="approve", approver="alice"), current_plan=scoped_state.plan)
    replay_state = make_run(deps.lineage.db, deps.store, REPO / "examples/briefs/synthetic.yaml", requester="replay", replay_of=original).model_copy(update={"plan": scoped_state.plan})
    assert gate_scope(replay_state, runtime).goto == "profile"
```

```python
# test_graph.py (@pytest.mark.db)
def test_graph_runs_to_first_gate_and_resumes(deps, checkpointer, synthetic_state) -> None:
    graph = build_graph(checkpointer); config = {"configurable": {"thread_id": synthetic_state.run_id}}
    result = graph.invoke(synthetic_state, config, context=deps)
    prompt = GatePrompt.model_validate(result["__interrupt__"][0].value)
    assert prompt.gate == "scope" and "plan" in prompt.summary
    result = graph.invoke(Command(resume=GateDecision(gate="scope", decision="approve", approver="alice").model_dump(mode="json")), config, context=deps)
    prompt = GatePrompt.model_validate(result["__interrupt__"][0].value)
    assert prompt.gate == "model" and set(result["refs"]) >= {"dataset", "split", "binning", "champion", "validation"}
    result = graph.invoke(Command(resume=GateDecision(gate="model", decision="reject", approver="bob").model_dump(mode="json")), config, context=deps)
    assert "__interrupt__" not in result and result["status"] == "rejected"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator/tests/test_gates.py packages/orchestrator/tests/test_graph.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `nodes/gates.py`, `graph.py` and the four placeholder nodes**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/orchestrator -v && make lint`
Expected: all pass (the graph test runs the full deterministic chain on the synthetic data, about a minute).

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator
git commit -m "feat(orchestrator): gate nodes as resumable interrupts and the LangGraph state machine"
```

---

### Task 17: Stub LLM nodes, seal, manifest and replay comparison

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/nodes/document.py`, `nodes/challenge.py`, `nodes/remediate.py`, `nodes/seal.py`, `fixtures/sections.yaml`, `fixtures/findings.yaml`, `replay.py`
- Modify: `packages/orchestrator/modelforge_orchestrator/graph.py` (wire the real nodes), `packages/orchestrator/pyproject.toml` (package data for `fixtures/`)
- Test: `packages/orchestrator/tests/test_stubs.py`, `test_seal.py`

**Interfaces:**
- Consumes: Task 10 models and tools, Task 12 schemas, `inventory.finalise`, `list_approvals`, `list_tool_calls`, `list_events`.
- Produces:
  - `document.build_sections(state: RunState, store: ArtifactStore) -> DocumentSections`: one `DocumentSection` per template section id from `fixtures/sections.yaml`, whose bodies are Python format strings over these names: `model_name`, `portfolio`, `intended_use`, `dataset`, `n_rows`, `target_rate`, `n_features_selected`, `features`, `overall`, `auc_oot`, `auc_val`, `fit_method`, `regulatory_scope`, and ids `profile_id`, `selection_id`, `champion_id`, `discrimination_id`, `validation_id`; numbers are formatted with `format_number(x) -> str` (4 significant figures; `"n/a"` for `None`/non-finite); every number in a body is followed by an `[A:<artifact_id>#<path>]` marker for its source (for example `[A:{discrimination_id}#by_split.oot.auc]`); the `regulatory_context` section cites `[K:sr11-7-overview]` and `[K:sr26-2-scope]` placeholders. `document.document(state, runtime)`: replay mode copies `refs["document_sections"]` from the original manifest; otherwise stores `build_sections` as `document_sections`; then `render_document(template_id="sr11-7-dev-doc", title=f"{plan.model_name} development document", context={"model": plan.model_name, "dataset": dataset_ref.as_string()})` and `check_completeness`; refs `document_sections`, `document_md`, `document_html`, `completeness`.
  - `challenge.build_findings(state, store) -> FindingsList` (round 1): always the finding from `fixtures/findings.yaml` with id `F-1` (category `data`, severity `medium`, the dataset-proxy limitation); plus `F-2` (category `performance`, severity `high`, calibration) when `refs["validation"].summary["statuses"]["cal.binomial"] != "pass"`; evidence lists the validation artifact id. `challenge.challenge(state, runtime)`: replay mode copies `refs["findings"]`; otherwise stores the list as `findings` (summary `{"n_findings", "max_severity"}`); in both modes inserts the `findings` rows (`status="open"`); `inventory` untouched.
  - `remediate.build_responses(findings: FindingsList) -> ResponsesList` (round 1; every finding `accept` with action `"Documented as a known limitation in the limitations section."`); `remediate.remediate(state, runtime)`: replay mode copies `refs["responses"]`; otherwise stores; updates `findings.status="accepted"` and `response`; returns `{"refs": ..., "counters": Counters(build_loops=state.counters.build_loops, remediation_rounds=1)}`.
  - `seal.build_manifest(state, deps, *, initial_plan_ref: str, outcome: Literal["completed", "rejected"]) -> Manifest` (`tool_versions` from `list_tool_calls` as `f"{server}/{tool}": version`; `approvals` from `list_approvals`; `events_head_hash` = last event hash at build time; `core_version`/`orchestrator_version` from the packages). `seal.seal(state, runtime) -> dict`: outcome = `"rejected"` if `state.status == "rejected"` else `"completed"`; stores the manifest (`set_manifest_hash`, `run.sealed {"manifest_id", "outcome"}`); when `replay_of is None`, finalises the inventory entry (`status`: `approved`, `approved_with_conditions` or `rejected` from `gates["signoff"]`, or `rejected` when no sign-off happened; `champion_ref`, `validation_summary = {"overall", "statuses"}`, `approver`, `conditions`, `approved_at = now` for approvals); when `replay_of` is set, computes `compare_refs(original_manifest, manifest) -> ReplayReport`, stores it as `replay_report`, appends `replay.completed {"ok", "n_mismatched"}` on both runs; returns `{"refs": {"manifest": ..., "replay_report"?: ...}, "status": outcome}`.
  - `replay.compare_refs(original: Manifest, replay: Manifest) -> ReplayReport` over `DETERMINISTIC_REFS` (`missing` = keys present in the original and absent in the replay; keys absent in both are ignored).
  - `graph.py` now imports the real four nodes.

- [ ] **Step 1: Write the failing tests**

```python
# test_stubs.py
def test_build_sections_is_grounded_and_deterministic(validated_state, deps) -> None:
    a = build_sections(validated_state, deps.store); b = build_sections(validated_state, deps.store)
    assert a == b and [s.section_id for s in a.sections] == [e["id"] for e in yaml.safe_load((REPO / "templates/sr11-7-dev-doc/sections.yaml").read_text())]
    perf = next(s for s in a.sections if s.section_id == "performance").body_md
    assert f"[A:{validated_state.refs['discrimination'].id}#by_split.oot.auc]" in perf and format_number(validated_state.refs["discrimination"].summary["auc"]["oot"]) in perf

def test_format_number() -> None:
    assert (format_number(0.812345), format_number(43405), format_number(float("nan")), format_number(None)) == ("0.8123", "43405", "n/a", "n/a")

def test_findings_depend_on_calibration_status(validated_state, deps) -> None:
    base = build_findings(validated_state, deps.store)
    assert [f.id for f in base.findings][0] == "F-1" and base.findings[0].category == "data"
    failing = validated_state.model_copy(update={"refs": {**validated_state.refs, "validation": validated_state.refs["validation"].model_copy(update={"summary": {**validated_state.refs["validation"].summary, "statuses": {**validated_state.refs["validation"].summary["statuses"], "cal.binomial": "fail"}}})}})
    assert [f.id for f in build_findings(failing, deps.store).findings] == ["F-1", "F-2"]
    responses = build_responses(base)
    assert all(r.decision == "accept" for r in responses.responses) and len(responses.responses) == len(base.findings)

@pytest.mark.db
def test_document_challenge_remediate_nodes(deps, runtime, validated_state) -> None:
    state = validated_state.model_copy(update={"refs": {**validated_state.refs, **document(validated_state, runtime)["refs"]}})
    assert deps.store.get(state.refs["document_md"].id).decode().startswith("# synthetic_pd development document") and state.refs["completeness"].summary["missing_sections"] == []
    state = state.model_copy(update={"refs": {**state.refs, **challenge(state, runtime)["refs"]}})
    update = remediate(state, runtime); state = state.model_copy(update={"refs": {**state.refs, **update["refs"]}, "counters": update["counters"]})
    assert state.counters.remediation_rounds == 1
    with deps.lineage.connection() as conn:
        rows = conn.execute("select status, response from findings where run_id = %s", (state.run_id,)).fetchall()
    assert rows and all(r["status"] == "accepted" and r["response"] for r in rows)
```

```python
# test_seal.py (@pytest.mark.db)
def test_seal_writes_manifest_and_finalises_inventory(deps, runtime, signed_off_state) -> None:   # fixture: full state with gates signoff=approve_with_conditions, status completed
    update = seal(signed_off_state, runtime)
    manifest = deps.store.get_model(update["refs"]["manifest"].id, Manifest)
    with deps.lineage.connection() as conn:
        run = get_run(conn, signed_off_state.run_id); entry = get_for_run(conn, run.id); events = list_events(conn, run.id)
    assert run.manifest_hash == update["refs"]["manifest"].id and manifest.run_id == run.id
    assert manifest.outcome == "completed" and set(manifest.tool_versions) >= {"credit-modeling/score", "validation/evaluate_thresholds"} and "ts" not in manifest.model_dump_json()
    assert entry.status == "approved_with_conditions" and entry.champion_ref == signed_off_state.refs["champion"].id and entry.validation_summary["overall"] in ("pass", "warn")
    assert any(e.type == "run.sealed" for e in events) and manifest.events_head_hash in {e.hash for e in events}

def test_compare_refs() -> None:
    ref = lambda c: ArtifactRef(id=c * 64, kind="k", media_type="m", size_bytes=1)
    base = dict(run_id="r", replay_of=None, brief_hash="h", dataset_ref=DatasetRef(adapter="synthetic_credit"), dataset_id="d", seed=42, profile="default", initial_plan_ref="p", plan_ref="p", tool_versions={}, approvals=[], outcome="completed", core_version="0", orchestrator_version="0", events_head_hash="e")
    original = Manifest(**base, refs={"profile": ref("a"), "split": ref("b"), "document_md": ref("c")})
    replay = Manifest(**base, run_id="r2", replay_of="r", refs={"profile": ref("a"), "split": ref("x")})
    report = compare_refs(original, replay)
    assert report.matched == ["profile"] and report.mismatched == [ReplayMismatch(ref="split", original_id="b" * 64, replay_id="x" * 64)] and report.missing == ["document_md"] and report.ok is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator/tests/test_stubs.py packages/orchestrator/tests/test_seal.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the four nodes, the fixtures, `replay.compare_refs`, and wire `graph.py`**

`fixtures/sections.yaml` bodies are plain English suited to a development document; the `limitations` section states the bankruptcy-as-default and horizon-files-as-vintages proxies; the `governance` section names the hash-chained event log and content-addressed artifacts and says replay is checked by `modelforge replay`.

- [ ] **Step 4: Run the orchestrator suite and lint**

Run: `uv run pytest packages/orchestrator -v && make lint`
Expected: all pass; `test_graph.py` still passes with the real nodes.

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator
git commit -m "feat(orchestrator): stub document, challenge and remediation nodes; seal with manifest, inventory and replay comparison"
```

---
### Task 18: Worker execution loop and replay creation

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/worker.py`
- Modify: `packages/orchestrator/modelforge_orchestrator/replay.py` (add `create_replay`)
- Test: `packages/orchestrator/tests/test_worker.py`

**Interfaces:**
- Consumes: `build_graph`, `PostgresSaver`, `StdioToolClient`, lineage `runs`, `queue`, `approvals`, `events`; `Settings`, `Deps`, `GatePrompt`, `ErrorRecord`.
- Produces:
  - `worker.worker_id() -> str` (`f"{socket.gethostname()}:{os.getpid()}"`).
  - `worker.initial_state(run: RunRecord) -> RunState`.
  - `worker.execute(settings: Settings, db: Database, store: ArtifactStore, queued: QueuedRun, *, tool_client_factory: Callable[[Settings], ToolClient] = StdioToolClient) -> RunRecord`: the six steps of spec 5.8. Resume input: when `run.status` is `awaiting_<gate>`, `Command(resume=list_approvals(conn, run_id, gate=gate)[-1].payload.model_dump(mode="json"))`; when `needs_attention`, `None`. After `graph.invoke`: an `__interrupt__` entry yields `GatePrompt` -> `gate.requested {"gate", "summary"}` event and status `awaiting_<gate>`; otherwise status = `result["status"]` (`completed` or `rejected`). On any exception: `ErrorRecord` (from `Step.last_error` when the failing step left one, else `kind="internal"`), `runs.set_error`, status `needs_attention`, `graph.update_state(config, {"errors": [record]})`. `finally`: `queue.release`. The tool client is entered for the duration of one `execute` call.
  - `worker.run_once(settings, db, store, *, run_id: str | None = None, **kw) -> RunRecord | None` (claims, executes, returns the run, or `None` when nothing was queued).
  - `worker.run_loop(settings, *, once: bool = False, poll_interval_s: float = 2.0) -> None` (migrate + `PostgresSaver.setup()` first; loops `run_once`; sleeps `poll_interval_s` when idle; returns after one pass when `once`).
  - `worker.setup_checkpointer(db: Database) -> PostgresSaver` (`PostgresSaver(db.pool)`; `.setup()` is called by `run_loop` and by the CLI `migrate`).
  - `replay.create_replay(conn, original_run_id: str) -> RunRecord` (requires original status `completed` or `rejected`, else `ReplayError(ValueError)`; `create_run(run_id=new_id("run"), requester="replay", profile, brief_hash, brief_ref, dataset_ref, seed, replay_of=original)`; `enqueue(kind="replay")`).

- [ ] **Step 1: Write the failing tests** (all `@pytest.mark.db`; `queued_run(db, store, brief)` helper creates and enqueues a run; `InProcessToolClient` is injected through `tool_client_factory`)

```python
def test_execute_parks_at_gates_and_completes(settings, database, store, checkpointer) -> None:
    run_id = queued_run(database, store, REPO / "examples/briefs/synthetic.yaml", requester="alice")
    run = run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    assert run.status == "awaiting_scope"
    with database.connection() as conn:
        assert [e.type for e in list_events(conn, run_id)][-2:] == ["gate.requested", "run.status_changed"] and claim(conn, "x") is None
    for gate, decision in (("scope", "approve"), ("model", "accept"), ("signoff", "approve")):
        with database.transaction() as conn:
            record_approval(conn, run_id, GateDecision(gate=gate, decision=decision, approver="bob"), current_plan=None); enqueue(conn, run_id, "resume")
        run = run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    assert run.status == "completed" and run.manifest_hash is not None
    with database.connection() as conn:
        assert get_for_run(conn, run_id).status == "approved"

def test_tool_failure_parks_run_and_resume_continues(settings, database, store, checkpointer, monkeypatch) -> None:
    run_id = queued_run(database, store, REPO / "examples/briefs/synthetic.yaml")
    calls = {"n": 0}
    real_call = InProcessToolClient.call
    def flaky(self, server, tool, request, **kw):
        if tool == "make_splits" and calls["n"] == 0:
            calls["n"] += 1; raise ToolError(server=server, tool=tool, version="1.0.0", code="transport", message="server died")
        return real_call(self, server, tool, request, **kw)
    monkeypatch.setattr(InProcessToolClient, "call", flaky)
    run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    with database.transaction() as conn:
        record_approval(conn, run_id, GateDecision(gate="scope", decision="approve", approver="alice"), current_plan=None); enqueue(conn, run_id, "resume")
    run = run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    assert run.status == "needs_attention" and run.error["kind"] == "tool" and run.error["error"]["code"] == "transport"
    with database.transaction() as conn:
        set_error(conn, run_id, None); enqueue(conn, run_id, "resume")
    run = run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    assert run.status == "awaiting_model"
    with database.connection() as conn:
        split_steps = [s for s in list_steps(conn, run_id) if s.node == "split"]
    assert [s.status for s in split_steps] == ["failed", "succeeded"] and [s.attempt for s in split_steps] == [1, 2]

def test_replay_reproduces_every_deterministic_ref(settings, database, store, checkpointer, completed_run_id) -> None:   # fixture: drives a fresh run through the three gates with run_once and returns its id
    with database.transaction() as conn:
        replay = create_replay(conn, completed_run_id)
    run = run_once(settings, database, store, run_id=replay.id, tool_client_factory=InProcessToolClient)
    assert run.status == "completed" and run.replay_of == completed_run_id
    with database.connection() as conn:
        report_ref = next(a for a in list_artifacts(conn, run.id) if a.kind == "replay_report")
        assert all(e.type != "gate.requested" for e in list_events(conn, run.id))
        assert get_for_run(conn, run.id) is None
    report = store.get_model(report_ref.id, ReplayReport)
    assert report.ok and report.mismatched == [] and report.missing == [] and {"champion", "champion_scores", "validation", "document_md"} <= set(report.matched)

def test_create_replay_requires_sealed_run(database, store) -> None:
    run_id = queued_run(database, store, REPO / "examples/briefs/synthetic.yaml")
    with database.transaction() as conn, pytest.raises(ReplayError):
        create_replay(conn, run_id)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator/tests/test_worker.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `worker.py` and `create_replay`**

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest packages/orchestrator -v && make lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator
git commit -m "feat(orchestrator): queue worker with gate parking, needs_attention recovery, and replay runs"
```

---

### Task 19: `modelforge` CLI and the end-to-end integration test

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/cli.py`
- Modify: `Makefile` (`migrate`, `worker`)
- Test: `packages/orchestrator/tests/test_cli.py`, `test_integration.py`

**Interfaces:**
- Consumes: everything above; `typer`, `typer.testing.CliRunner`.
- Produces: `cli.app: typer.Typer` with the commands of spec 5.9 and exit codes 0 / 1 / 2. Shared helpers: `_context() -> tuple[Settings, Database, ArtifactStore]` (reads `load_settings()`, configures logging), `_print_run(run: RunRecord, *, as_json: bool)`. Behaviour: `run BRIEF [--requester NAME] [--profile P] [--detach] [--json]` validates the brief (`BriefError` -> message on stderr, exit 2), rejects an unknown adapter via `modelforge_core.datasets.registry.ADAPTERS` (exit 2), stores the brief bytes as a `brief` artifact (`MEDIA_YAML`), creates the run (requester default `os.environ.get("USER", "requester")`, `seed = brief.dataset.seed`), enqueues `start`, then unless `--detach` calls `run_once(run_id=...)` and prints the status plus, when parked at a gate, the gate name and the summary keys; `approve RUN_ID GATE --decision D --approver NAME [--comment C] [--plan plan.yaml] [--adjust-target features|build] [--condition TEXT]... [--detach]` builds the `GateDecision` (the plan file parsed as `ModelingPlan`), calls `record_approval` (`ApprovalError` subclasses -> message, exit 2), enqueues `resume`, foreground-executes unless `--detach`; `resume RUN_ID [--detach]` (only from `needs_attention`, else exit 2; clears the error, enqueues `resume`); `abort RUN_ID` (sets `aborted`; exit 2 if the run is `completed` or `rejected`); `status RUN_ID [--json]` prints status, replay_of, error, steps (`node attempt status`), and refs with summaries; `worker [--once] [--poll-interval S]`; `migrate`; `replay RUN_ID [--detach] [--json]` (creates the replay run, foreground-executes unless `--detach`, prints the `ReplayReport`, exit 1 when not `ok`); `verify RUN_ID [--json]` (prints the `VerifyReport`, exit 1 when not `ok`). Makefile: `migrate: uv run modelforge migrate`, `worker: uv run modelforge worker`.

- [ ] **Step 1: Write the failing tests**

`test_cli.py` uses `CliRunner` with `env={"MODELFORGE_DATABASE_URL": <temporary db>, "MODELFORGE_ARTIFACT_DIR": ..., "MODELFORGE_DATA_DIR": ..., "MODELFORGE_TEMPLATES_DIR": ...}` and the in-process checkpointer setup from the session fixture; `run_id_from(output: str) -> str` parses `run_[0-9a-f]{12}`.

```python
@pytest.mark.db
def test_cli_run_unknown_adapter_exits_2(runner, env, tmp_path) -> None:
    brief = tmp_path / "b.yaml"; brief.write_text((REPO / "examples/briefs/synthetic.yaml").read_text().replace("synthetic_credit", "nope"))
    result = runner.invoke(app, ["run", str(brief)], env=env)
    assert result.exit_code == 2 and "nope" in result.output

@pytest.mark.db
def test_cli_approve_wrong_gate_exits_2_and_four_eyes(runner, env) -> None:
    result = runner.invoke(app, ["run", str(REPO / "examples/briefs/synthetic.yaml"), "--requester", "alice", "--detach"], env=env)
    run_id = run_id_from(result.output)
    assert runner.invoke(app, ["approve", run_id, "model", "--decision", "accept", "--approver", "bob"], env=env).exit_code == 2   # queued, not awaiting
    assert runner.invoke(app, ["status", run_id], env=env).output.splitlines()[0].endswith("queued")

@pytest.mark.db
def test_cli_verify_detects_tamper(runner, env, completed_cli_run, store) -> None:
    assert runner.invoke(app, ["verify", completed_cli_run], env=env).exit_code == 0
    with psycopg.connect(env["MODELFORGE_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("update events set payload = '{}' where run_id = %s and type = 'run.created'", (completed_cli_run,))
    result = runner.invoke(app, ["verify", completed_cli_run, "--json"], env=env)
    assert result.exit_code == 1 and json.loads(result.output)["chain"]["ok"] is False
```

`test_integration.py` (`@pytest.mark.db`; real stdio servers through the CLI, no `tool_client_factory` override; not marked `slow`, since the synthetic run takes about a minute and belongs in the default suite):

```python
def test_full_run_through_three_gates_then_replay_and_verify(runner, env) -> None:
    out = runner.invoke(app, ["run", str(REPO / "examples/briefs/synthetic.yaml"), "--requester", "alice"], env=env)
    assert out.exit_code == 0 and "awaiting_scope" in out.output
    run_id = run_id_from(out.output)
    assert runner.invoke(app, ["approve", run_id, "model", "--decision", "accept", "--approver", "bob"], env=env).exit_code == 2   # wrong gate
    out = runner.invoke(app, ["approve", run_id, "scope", "--decision", "approve", "--approver", "alice"], env=env)
    assert out.exit_code == 0 and "awaiting_model" in out.output
    assert runner.invoke(app, ["approve", run_id, "model", "--decision", "accept", "--approver", "alice"], env=env).exit_code == 2   # four eyes
    out = runner.invoke(app, ["approve", run_id, "model", "--decision", "accept", "--approver", "bob"], env=env)
    assert out.exit_code == 0 and "awaiting_signoff" in out.output
    out = runner.invoke(app, ["approve", run_id, "signoff", "--decision", "approve_with_conditions", "--approver", "bob", "--condition", "monitor PSI quarterly"], env=env)
    assert out.exit_code == 0 and "completed" in out.output
    status = runner.invoke(app, ["status", run_id, "--json"], env=env)
    assert status.exit_code == 0 and json.loads(status.output)["run"]["status"] == "completed"
    replay = runner.invoke(app, ["replay", run_id, "--json"], env=env)
    assert replay.exit_code == 0 and json.loads(replay.output)["ok"] is True
    assert runner.invoke(app, ["verify", run_id], env=env).exit_code == 0
    assert not subprocess.run(["pgrep", "-f", "modelforge_mcp"], capture_output=True).stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/orchestrator/tests/test_cli.py packages/orchestrator/tests/test_integration.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `cli.py`; add the Makefile targets**

`status --json` prints `{"run": RunRecord, "steps": [...], "refs": {name: ArtifactRef}}` as JSON; refs come from the graph state (`graph.get_state(config).values["refs"]`).

- [ ] **Step 4: Run tests and lint; then exercise the CLI by hand on the synthetic brief**

Run: `uv run pytest packages/orchestrator -v && make lint`
Expected: all pass.
Run: `make db-up && uv run modelforge migrate && uv run modelforge run examples/briefs/synthetic.yaml --requester alice`
Expected: the run id and `awaiting_scope` printed; then `approve` through the three gates as in the integration test prints `completed`; `uv run modelforge replay <run_id>` prints `ok: True`; `uv run modelforge verify <run_id>` prints `ok: True`.

- [ ] **Step 5: Commit**

```bash
git add packages/orchestrator Makefile
git commit -m "feat(orchestrator): modelforge CLI (run, approve, resume, abort, status, worker, migrate, replay, verify) and end-to-end test"
```

---

### Task 20: Dockerfile, worker service, CI with Postgres and Docker build

**Files:**
- Create: `Dockerfile`, `.dockerignore`
- Modify: `docker-compose.yml`, `.github/workflows/ci.yml`, `Makefile`

**Interfaces:**
- Produces: image `modelforge` whose entrypoint is `uv run modelforge`; Compose service `worker` (command `worker`); CI job `lint-test` with a Postgres service and `MODELFORGE_REQUIRE_DB=1`; CI job `docker-build`; Makefile `up: docker compose up -d --build --wait`, `down: docker compose down`.

- [ ] **Step 1: Write the `Dockerfile` and `.dockerignore`**

`FROM python:3.12-slim`; `apt-get install -y --no-install-recommends libgomp1` (LightGBM) then clean; `COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /uvx /bin/`; `WORKDIR /app`; copy `pyproject.toml`, `uv.lock`, `.python-version`, `packages/`, `templates/`, `examples/`; `ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_PREFERENCE=only-system`; `RUN uv sync --frozen --no-dev`; `ENV MODELFORGE_ARTIFACT_DIR=/data/artifacts MODELFORGE_DATA_DIR=/data/datasets MODELFORGE_TEMPLATES_DIR=/app/templates`; `ENTRYPOINT ["uv", "run", "--no-sync", "modelforge"]`; `CMD ["worker"]`. `.dockerignore`: `.git`, `.venv`, `data`, `artifacts`, `out`, `node_modules`, `.superpowers`, `notes`, `**/__pycache__`, `.pytest_cache`, `.ruff_cache`.

- [ ] **Step 2: Add the `worker` service to `docker-compose.yml`**

`build: .`, `depends_on: postgres: condition: service_healthy`, `environment: MODELFORGE_DATABASE_URL: postgresql://modelforge:modelforge@postgres:5432/modelforge`, `volumes: ["./artifacts:/data/artifacts", "./data:/data/datasets"]`, `command: ["worker"]`, `restart: unless-stopped`.

- [ ] **Step 3: Update `ci.yml`**

Job `lint-test`: add `services.postgres` (`image: pgvector/pgvector:pg16`, `env` user/password/db `modelforge`, `ports: ["5432:5432"]`, `options: --health-cmd "pg_isready -U modelforge" --health-interval 5s --health-timeout 3s --health-retries 20`); job-level `env`: `MODELFORGE_DATABASE_URL: postgresql://modelforge:modelforge@localhost:5432/modelforge`, `MODELFORGE_REQUIRE_DB: "1"`; after `make test`, a step `uv run modelforge-mcp goldens --artifact-dir /tmp/goldens-artifacts` followed by `actions/upload-artifact@v4` of `packages/mcp/contracts/goldens/` named `goldens-linux`. New job `docker-build` (`runs-on: ubuntu-latest`): checkout, `docker/setup-buildx-action@v3`, `docker build -t modelforge:ci .`.

- [ ] **Step 4: Build the image locally and run a worker container against Compose Postgres**

Run: `docker build -t modelforge:local . && make up && docker compose logs --tail 20 worker`
Expected: the build succeeds; the worker log shows migrations applied and an idle poll line; `docker compose exec worker modelforge --help` lists the commands. Then `make down`.

- [ ] **Step 5: Lint, run the suite, commit**

Run: `make lint && uv run pytest -m "not slow"`
Expected: all pass (the test suite does not depend on Docker beyond Postgres).

```bash
git add Dockerfile .dockerignore docker-compose.yml .github/workflows/ci.yml Makefile
git commit -m "chore: worker image and Compose service; CI Postgres service, golden upload and Docker build"
```

---

### Task 21: Polish brief end to end, README

**Files:**
- Create: `packages/orchestrator/tests/test_polish_run.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the CLI; `data/` cache (`MODELFORGE_DATA_DIR`).

- [ ] **Step 1: Write the slow test**

```python
@pytest.mark.slow
@pytest.mark.db
def test_polish_brief_runs_to_completion_and_replays(runner, env_with_real_data) -> None:
    out = runner.invoke(app, ["run", str(REPO / "examples/briefs/polish-pd.yaml"), "--requester", "alice"], env=env_with_real_data)
    run_id = run_id_from(out.output)
    for gate, decision, approver in (("scope", "approve", "alice"), ("model", "accept", "bob"), ("signoff", "approve", "bob")):
        assert runner.invoke(app, ["approve", run_id, gate, "--decision", decision, "--approver", approver], env=env_with_real_data).exit_code == 0
    status = json.loads(runner.invoke(app, ["status", run_id, "--json"], env=env_with_real_data).output)
    assert status["run"]["status"] == "completed"
    assert status["refs"]["discrimination"]["summary"]["auc"]["oot"] > 0.65 and status["refs"]["validation"]["summary"]["overall"] == "fail"
    assert json.loads(runner.invoke(app, ["replay", run_id, "--json"], env=env_with_real_data).output)["ok"] is True
```

The `overall == "fail"` expectation is the SP1 hand-off's real-data result (binomial calibration fails on the out-of-time vintage); if SP1's number has moved, record the new value in the hand-off note rather than loosening the test.

- [ ] **Step 2: Run it**

Run: `uv run pytest packages/orchestrator/tests/test_polish_run.py -v`
Expected: 1 passed (several minutes: two full fits on 43k rows).

- [ ] **Step 3: Update `README.md`**

Add after the core paragraph: one paragraph on SP2 (governed run, lineage, MCP servers, gates, replay); a "Run a governed model build" section with `make db-up`, `uv run modelforge migrate`, `uv run modelforge run examples/briefs/synthetic.yaml --requester alice`, the three `approve` commands, `status`, `replay`, `verify`; an "MCP servers" section listing the four servers, the `uv run modelforge-mcp <server>` command, the two environment variables, and a Claude Desktop configuration snippet; a "Compose" section (`make up` / `make down`); the `db` marker and `MODELFORGE_REQUIRE_DB` note under Commands; and the Status line updated to "Sub-projects 1 and 2 complete" with a pointer to the SP2 spec and this plan. Keep the repository employer-neutral.

- [ ] **Step 4: Full gate**

Run: `make lint && make test`
Expected: lint clean; every test passes including slow ones.

- [ ] **Step 5: Commit**

```bash
git add README.md packages/orchestrator
git commit -m "test(orchestrator): Polish brief end to end; README for the governed run and MCP servers"
```

---

## Self-review notes

- **Spec coverage.** Section 2 (packages, import rules) is Task 1. Section 3: 3.1 to 3.2 Task 3, 3.3 and 3.5 Task 5, 3.4 Task 4, 3.6 to 3.8 Task 6, 3.9 Task 3. Section 4: 4.1 and 4.2 Task 7, 4.3 Task 8, 4.4 Task 9, 4.5 Task 10, 4.6 Tasks 7 and 11, 4.7 Task 2. Section 5: 5.1 and 5.11 Task 12, 5.3 Task 13, 5.5 Task 14, 5.2 Tasks 14 to 17, 5.4 Task 16, 5.6 and 5.7 (comparison) Task 17, 5.7 (creation) and 5.8 Task 18, 5.9 and 5.10 Tasks 19 and 12. Section 6 Tasks 1 and 20. Section 7 is spread over every task's tests; the contract snapshots are Task 11 and the integration run Task 19.
- **Deviation from the spec, recorded as a ruling:** `PostgresSaver.setup()` is called by the orchestrator (`worker.run_loop`, CLI `migrate`, the orchestrator test fixture), not by `modelforge_lineage.migrate.apply`, because lineage may not import LangGraph. The spec was amended in the same commit as this plan.
- **Type consistency.** `ArtifactRef`, `Producer`, `GateDecision`, `GateName`, `RunStatus`, `QueuedRun`, `RunRecord` are defined once in Task 3 and used by name afterwards. `ToolCall`, `ToolError`, `ToolClient` are defined in Task 13 and consumed by Tasks 14 to 18. `DatasetRef` lives in `modelforge_mcp.schemas` and is imported by the orchestrator. The `RefName` keys used by nodes (Tasks 14 to 17) are exactly the Literal of Task 12.
- **Review Focus.** Item 1: Task 13 `test_dead_server_is_transport_error_and_no_orphans`, Task 18 `test_tool_failure_parks_run_and_resume_continues`. Item 2: Task 6 `test_record_approval_rejects_wrong_gate`, Task 19 `test_cli_approve_wrong_gate_exits_2_and_four_eyes`. Item 3: Task 7 `test_make_splits_missing_vintage_is_invalid_input`, Task 19 `test_cli_run_unknown_adapter_exits_2`. Item 4: Task 5 `test_concurrent_claims_take_different_runs`. Item 5: Task 6 `test_verify_run_reports_tampered_artifact_and_event`, Task 19 `test_cli_verify_detects_tamper`.
- **Deliberately open to the implementer:** SQL formatting and index names; helper decomposition inside modules; the exact guidance sentences in `sections.yaml` and the fixture prose; Typer option help text; the HTML stylesheet; how test fixtures share the prepared states across the orchestrator test modules (a `conftest.py` fixture chain is expected).
