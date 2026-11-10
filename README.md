# ModelForge

ModelForge is a governed, multi-agent copilot for the credit risk model development lifecycle. Given a raw credit dataset and a modeling brief, it produces a validated probability-of-default (PD) model, an SR 11-7 style model development document, an independent "effective challenge" review, a tamper-evident audit trail, and a model inventory entry, with human approval gates at the points where a bank would require them.

The core toolkit (`packages/core`, `modelforge_core`) is the deterministic, LLM-free layer underneath. It loads a dataset into a canonical schema, profiles it and screens for leakage, builds train / validation / out-of-time splits, fits monotone WoE binning and IV / correlation feature selection, trains a WoE logistic scorecard champion and a LightGBM challenger, calibrates the champion, and validates it: discrimination (AUC, KS, Gini with bootstrap intervals), calibration (per-grade binomial, Hosmer-Lemeshow, expected / observed), stability (PSI, CSI), sensitivity (coefficient signs, WoE monotonicity, bin shifts), and a champion-challenger benchmark. The champion is refit iteratively, dropping any feature whose coefficient has the wrong sign, and these drops are recorded in the feature selection artifact. Every result is checked against documented thresholds into a single validation report. Models are stored as JSON (scorecard) and LightGBM native text (challenger); nothing is pickled.

The orchestrator (`packages/orchestrator`, `modelforge_orchestrator`) turns that toolkit into a governed run. A YAML brief starts a LangGraph run whose nodes call the toolkit only through MCP servers (`packages/mcp`). Every input and output is a content-addressed artifact, and every step, tool call and decision is an event in a hash-chained lineage store in Postgres (`packages/lineage`). The run stops at three human gates (scope, model, sign-off), with four-eyes checks on the last two, and a sealed run can be replayed to reproduce every deterministic artifact hash. This is a reference implementation, not a production system: sub-project 3 added the LangChain model gateway, through which the scope and documentation agents now run, while the `challenge` and `remediate` nodes remain deterministic stubs until sub-project 4.

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

`edit` and `adjust` take a modeling plan with `--plan`; `approve_with_conditions` takes one or more `--condition`. The `model` and `signoff` decisions must come from someone other than the requester. Run statuses are `queued`, `running`, `awaiting_scope`, `awaiting_model`, `awaiting_signoff`, `needs_attention`, `completed`, `rejected` and `aborted`. A run that fails a tool call parks in `needs_attention`; `uv run modelforge resume <run_id>` retries it from its last checkpoint and `uv run modelforge abort <run_id>` ends it. A run parked by its cost cap resumes with a higher cap: `uv run modelforge resume <run_id> --cost-cap 20` sets the cap in USD before re-enqueueing (allowed only from `needs_attention`). `run`, `approve`, `resume` and `replay` accept `--detach` to only enqueue and leave execution to a worker (`uv run modelforge worker`).

The run manifest records every artifact hash, and `replay` reproduces each deterministic artifact byte for byte. A plain replay does not call a model: it copies the original's modeling plan, scope notes and document sections and re-renders the document, so the document refs match too. `--rerun-llm` runs the scope and documentation agents again; the rendered document and completeness refs are then listed under `regenerated` instead of being compared, and `ok` ignores them. `verify` exits non-zero if an event or artifact has been altered.

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

Every model call goes through one gateway (`packages/gateway`, `modelforge_gateway`) built on LangChain chat models: `langchain-anthropic` for Anthropic, `langchain-ollama` for Ollama and `langchain-openai` pointed at LM Studio's OpenAI-compatible local server. No node imports a provider SDK. Each call names a purpose (`scope`, `document`, and later `challenge`, `remediate`, `judge`), and the run's profile routes that purpose to a provider, model and effort. The output is structured: the gateway validates it against a Pydantic schema and retries a failed validation at most twice.

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

**The `fake` profile.** `fake` is a scripted stand-in, not a recording of a real model. It reads the fenced blocks the prompts mark (the brief, the defaults, the artifact excerpts and the retrieved passages) and builds its answers deterministically from them: the modeling plan the brief implies, a fixed set of queries per section, and sections written from a script that copies numbers out of the excerpts with the same rounding rule the grounding check applies. It needs no network and costs nothing. It is the default profile for the test suite and for the Docker image, so everything runs offline; it shows that the pipeline, lineage and checks work, not how well any model writes.

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

Then approve the three gates as in "Run a governed model build". `MODELFORGE_OLLAMA_MODEL` and `MODELFORGE_LMSTUDIO_MODEL` run a different local model under the same profile, and the base URL variables point at another host. Ollama runs with a 32768-token context window unless `MODELFORGE_OLLAMA_NUM_CTX` sets another; pick one the model and the machine's memory support.

Every provider returns structured output through its integration's `json_schema` method by default, Anthropic included (forced tool calling is unsupported when thinking is enabled). A route's `structured` key overrides that: the Anthropic profiles set `structured: function_calling` on the `scope` route, because the modeling-plan schema exceeds the structured-output grammar's complexity limit, while the document routes keep `json_schema`. A section-writing call may use up to 8192 output tokens and a query-planning call up to 1024; on a thinking model the thinking tokens count against that limit.

Results on the Polish brief through the three gates:

| Profile | Model | Outcome | Flagged sections | Ungrounded numbers | Cost (USD) | Wall time |
| --- | --- | --- | --- | --- | --- | --- |
| `default` | `claude-opus-5-5` | to be measured | to be measured | to be measured | to be measured | to be measured |
| `local-ollama` | `gpt-oss:20b` | to be measured | to be measured | to be measured | to be measured | to be measured |
| `local-lmstudio` | `google/gemma-4-26b-a4b-qat` | to be measured | to be measured | to be measured | to be measured | to be measured |

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

Sub-projects 1 to 3 complete: the core toolkit; the governed orchestrator with its lineage store and MCP servers; and the LLM gateway, knowledge layer, and scope and documentation agents. See the architecture spec at `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md`, the orchestrator spec at `docs/superpowers/specs/2026-11-09-sp2-orchestrator-design.md`, the gateway and agents spec at `docs/superpowers/specs/2026-11-09-sp3-gateway-knowledge-agents-design.md`, and the implementation plans at `docs/superpowers/plans/2026-11-09-sp1-core-toolkit.md`, `docs/superpowers/plans/2026-11-09-sp2-orchestrator.md` and `docs/superpowers/plans/2026-11-09-sp3-gateway-knowledge-agents.md`.
