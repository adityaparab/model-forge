# ModelForge

ModelForge is a governed, multi-agent copilot for the credit risk model development lifecycle. Given a raw credit dataset and a modeling brief, it produces a validated probability-of-default (PD) model, an SR 11-7 style model development document, an independent "effective challenge" review, a tamper-evident audit trail, and a model inventory entry, with human approval gates at the points where a bank would require them.

The core toolkit (`packages/core`, `modelforge_core`) is the deterministic, LLM-free layer underneath. It loads a dataset into a canonical schema, profiles it and screens for leakage, builds train / validation / out-of-time splits, fits monotone WoE binning and IV / correlation feature selection, trains a WoE logistic scorecard champion and a LightGBM challenger, calibrates the champion, and validates it: discrimination (AUC, KS, Gini with bootstrap intervals), calibration (per-grade binomial, Hosmer-Lemeshow, expected / observed), stability (PSI, CSI), sensitivity (coefficient signs, WoE monotonicity, bin shifts), and a champion-challenger benchmark. The champion is refit iteratively, dropping any feature whose coefficient has the wrong sign, and these drops are recorded in the feature selection artifact. Every result is checked against documented thresholds into a single validation report. Models are stored as JSON (scorecard) and LightGBM native text (challenger); nothing is pickled.

The orchestrator (`packages/orchestrator`, `modelforge_orchestrator`) turns that toolkit into a governed run. A YAML brief starts a LangGraph run whose nodes call the toolkit only through four MCP servers (`packages/mcp`). Every input and output is a content-addressed artifact, and every step, tool call and decision is an event in a hash-chained lineage store in Postgres (`packages/lineage`). The run stops at three human gates (scope, model, sign-off), with four-eyes checks on the last two, and a sealed run can be replayed to reproduce every deterministic artifact hash. This is a reference implementation, not a production system: in sub-project 2 the LLM nodes (scope, document, challenge, remediate) are deterministic stubs, and the LangChain model gateway arrives in sub-project 3.

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
uv run modelforge verify <run_id>          # walk the event hash chain, artifacts and manifest
```

`examples/briefs/polish-pd.yaml` runs the same sequence on the Polish data. Gate decisions:

| Gate | Decisions |
| --- | --- |
| `scope` | `approve`, `edit`, `reject` |
| `model` | `accept`, `adjust`, `reject` |
| `signoff` | `approve`, `approve_with_conditions`, `reject` |

`edit` and `adjust` take a modeling plan with `--plan`; `approve_with_conditions` takes one or more `--condition`. The `model` and `signoff` decisions must come from someone other than the requester. Run statuses are `queued`, `running`, `awaiting_scope`, `awaiting_model`, `awaiting_signoff`, `needs_attention`, `completed`, `rejected` and `aborted`. A run that fails a tool call parks in `needs_attention`; `uv run modelforge resume <run_id>` retries it from its last checkpoint and `uv run modelforge abort <run_id>` ends it. `run`, `approve`, `resume` and `replay` accept `--detach` to only enqueue and leave execution to a worker (`uv run modelforge worker`).

The run manifest records every artifact hash, and `replay` reproduces each deterministic artifact byte for byte (the stub LLM nodes are deterministic too). `verify` exits non-zero if an event or artifact has been altered.

## MCP servers

The toolkit is exposed through four MCP servers over stdio: `data-profiler`, `credit-modeling`, `validation` and `doc-renderer`. Each runs with:

```bash
uv run modelforge-mcp <server>
```

They read `MODELFORGE_ARTIFACT_DIR` (the artifact store) and `MODELFORGE_DATA_DIR` (the dataset cache); `doc-renderer` also reads `MODELFORGE_TEMPLATES_DIR`. To use a server from a desktop MCP client, add it to the client's configuration, for example:

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

## Compose

```bash
make up     # build the image and start Postgres and the worker
make down   # stop the stack
```

The image runs the `modelforge` CLI with `worker` as its default command. The worker service connects to the Compose Postgres, mounts `./artifacts` and `./data`, and executes runs submitted with `--detach`; `docker compose logs worker` shows its JSON logs, starting with a `worker.ready` line.

## Configuration

The orchestrator reads these environment variables (a `.env` file fills any that are unset):

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
make up         # start Postgres and the worker in Compose
make down       # stop the Compose stack
```

Tests marked `db` need Postgres and skip when it is unreachable; set `MODELFORGE_REQUIRE_DB=1` to make them fail instead (CI does).

## Datasets

- Polish companies bankruptcy data, UCI Machine Learning Repository, DOI 10.24432/C5F600, licensed CC BY 4.0.
- Taiwanese bankruptcy prediction, UCI Machine Learning Repository, DOI 10.24432/C5004D, licensed CC BY 4.0.

Both are corporate bankruptcy datasets, not retail credit data, so two proxies are documented and applied:

- **Bankruptcy as default.** The bankruptcy label stands in for the default flag of a PD model.
- **Horizon files as vintages.** The Polish data ships as five files by forecasting horizon (`1year` to `5year`); each file is treated as a vintage, so a held-out file gives an out-of-time split. The Taiwanese set has no time dimension at all, so it uses a stratified random split and every out-of-time test is reported as not applicable.

## Status

Sub-projects 1 and 2 complete: the core toolkit, and the governed orchestrator with its lineage store and MCP servers. See the architecture spec at `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md`, the orchestrator spec at `docs/superpowers/specs/2026-11-09-sp2-orchestrator-design.md`, and the implementation plans at `docs/superpowers/plans/2026-11-09-sp1-core-toolkit.md` and `docs/superpowers/plans/2026-11-09-sp2-orchestrator.md`.
