# SP3: LLM Gateway, Knowledge Layer, Scope Agent and Documentation Agent

**Status:** approved in brainstorming, awaiting written review
**Date:** 2026-11-10 (brainstorm and spec; the SP3 documents keep the `2026-11-09` file prefix the umbrella plan assigned)
**Parent:** `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` (sections 4.1, 4.5 as ruled in SP2, 7, 8.1, 8.2, 9, 10.1, 15, 17 row 3); `docs/superpowers/specs/2026-11-09-sp2-orchestrator-design.md` section 10; `docs/superpowers/notes/2026-11-09-sp2-handoff.md`.

---

## 1. Purpose and done criteria

SP3 replaces the first two stub LLM nodes of the SP2 graph with real agents behind a governed LLM gateway, adds the regulatory and methodology knowledge layer with its MCP server, and extends replay with `--rerun-llm`. `challenge` and `remediate` stay deterministic stubs for SP4.

After SP3:

1. `MODELFORGE_PROFILE=fake uv run modelforge run examples/briefs/polish-pd.yaml` runs to completion through the three gates with a scope agent plan, an eleven-section development document written section by section through the gateway, grounding checks that pass on every section, `llm_calls` and `prompts` rows for every call, and a `knowledge_index` artifact in the manifest. No API key and no network are needed.
2. The same brief runs against Anthropic (`MODELFORGE_PROFILE=default`, `ANTHROPIC_API_KEY` in `.env`), Ollama (`local-ollama`, `gpt-oss:20b`) and LM Studio (`local-lmstudio`, `google/gemma-4-26b-a4b-qat`) by changing one environment variable; the README documents the path and the measured outcome for each.
3. `modelforge replay <run_id>` reproduces every deterministic artifact id, including the knowledge index; `modelforge replay <run_id> --rerun-llm` regenerates the plan and the document through the gateway and reports the regenerated refs separately.
4. The per-run cost cap parks a run in `needs_attention`; `modelforge resume <run_id> --cost-cap <usd>` raises the cap and continues.
5. Redaction refuses an outbound prompt that carries a restricted term or an identifier-like token, with a typed error that names the pattern and never the value.
6. `make lint` is clean with the extended import-linter contracts; `MODELFORGE_REQUIRE_DB=1 make test` passes with the full suite.

## 2. Packages and dependency rules

Two new workspace members join `packages/`:

```
packages/gateway/    modelforge_gateway    CompletionRequest/Result, profiles, prompt registry, redaction, pricing and cost, providers (anthropic, ollama, lmstudio, fake), Gateway
packages/knowledge/  modelforge_knowledge  corpus loading, chunking, citation ids, BM25 retrieval, KnowledgeIndex artifact model
```

Repository assets: `prompts/` (versioned prompt files), `profiles/` (routing profiles), `knowledge-base/` (the corpus). The Dockerfile copies all three next to `templates/`.

| Package | Depends on |
|---|---|
| `modelforge_gateway` | `modelforge-core`, `modelforge-lineage`, `langchain-core>=1.7`, `langchain-anthropic>=1.7`, `langchain-ollama>=1.1`, `langchain-openai>=1.7`, `pyyaml>=6`, `structlog>=26` |
| `modelforge_knowledge` | `modelforge-core`, `modelforge-lineage` (artifact store and models only), `pyyaml>=6` |
| `modelforge_mcp` | adds `modelforge-knowledge` |
| `modelforge_orchestrator` | adds `modelforge-gateway` |

Import-linter contracts (root packages become the six `modelforge_*` packages):

- Layers: `modelforge_orchestrator` > `modelforge_mcp | modelforge_gateway` > `modelforge_knowledge` > `modelforge_lineage` > `modelforge_core`. `mcp` and `gateway` are independent siblings; neither imports the other.
- `modelforge_core` keeps its forbidden list (now also `modelforge_gateway`, `modelforge_knowledge`).
- `modelforge_lineage` additionally may not import `modelforge_gateway`, `modelforge_knowledge`.
- `modelforge_mcp` keeps LangGraph-free and database-driver-free; `modelforge_knowledge` is LangChain-free (`langchain*`, `langgraph`), database-driver-free (`psycopg`) and MCP-free (`mcp`).
- Only `modelforge_gateway.providers` imports `langchain_anthropic`, `langchain_ollama`, `langchain_openai`.
- No package imports `anthropic`, `openai`, `ollama`, `httpx` or `requests` (core's download module keeps its single exception).

The no-pickle scan covers the two new packages.

## 3. Gateway

### 3.1 Interface

```python
Purpose = Literal["scope", "document", "challenge", "remediate", "judge"]
Effort = Literal["low", "medium", "high"]

class Message(StrictModel):
    role: Literal["system", "user"]
    content: str

class CallMetadata(StrictModel):
    run_id: str
    step_id: str
    section_id: str | None = None

class RedactionContext(StrictModel):
    restricted_terms: list[str] = []          # original column names, brief restricted_terms

class CompletionRequest(StrictModel):
    purpose: Purpose
    prompt_name: str                          # "<agent>/<name>", resolved by the registry
    prompt_version: int
    system: str                               # rendered system prompt (stable, cacheable)
    messages: list[Message]                   # the user turn(s)
    output_schema: type[BaseModel]            # structured output; every SP3 call is structured
    effort: Effort | None = None              # None: the profile's effort for the purpose
    max_output_tokens: int = 4096
    cache_prefix: bool = True
    redaction: RedactionContext = RedactionContext()
    metadata: CallMetadata

class Usage(StrictModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

class CompletionResult(StrictModel):
    text: str | None
    parsed: BaseModel
    provider: str
    model: str
    prompt_name: str
    prompt_version: int
    prompt_hash: str                          # sha256 of prompt file bytes
    request_hash: str                         # sha256 of canonical JSON of the final messages
    usage: Usage
    cost_usd: float
    latency_ms: int
    stop_reason: str | None
    schema_valid: bool                        # True after at most two retries; else GatewayError
    attempts: int
    raw_ref: ArtifactRef                      # kind llm_response
```

`GatewayError(kind, message)` with `kind` in `schema` (output still invalid after two retries), `refusal` (the provider reported a safety refusal), `cost_cap` (the projected spend would exceed the run's cap), `redaction` (a restricted term or identifier-like token in the outbound text), `provider` (transport or provider error after the integration's own retries), `config` (unknown profile, missing credential, unknown prompt). Every kind parks the run in `needs_attention` through the existing `Step` error path with `ErrorRecord.kind="llm"` and the error dict `{kind, message, purpose, provider, model}`.

The gateway is a plain class, `Gateway.complete(request) -> CompletionResult`, constructed once per executed run: `Gateway(settings, profile, lineage, store, registry)`. It is synchronous and uses `invoke`; streaming is not used (the umbrella spec's "streaming for document sections" is replaced by structured output per section, amendment in section 12). The umbrella spec's "gateway as a Runnable with a callback handler" becomes the inline pipeline below; the chat models underneath remain LangChain runnables.

### 3.2 Pipeline

For each `complete` call, in order:

1. **Route.** `profile.routes[purpose]` gives `provider`, `model`, `effort`; the request's `effort` overrides. Unknown purpose or profile is `config`.
2. **Prompt.** The registry returns the prompt record for `(prompt_name, prompt_version)`; the record's hash is `prompt_hash`, and the `prompts` row is upserted (section 3.5). The caller renders the prompt text itself; the gateway records which file was used.
3. **Messages.** `SystemMessage(system)` and one `HumanMessage` per user message. With `cache_prefix` on an Anthropic route, the system content is a single text block carrying `cache_control: {type: ephemeral}`.
4. **Redaction scan** over every message's text (section 3.6). A hit is `GatewayError("redaction")`; nothing is sent.
5. **Cost cap.** Read `runs.cost_usd` and `runs.cost_cap_usd`. Projected spend is `len(all text) / 4` input tokens at the input price plus `max_output_tokens` at the output price. `spent + projected > cap` is `GatewayError("cost_cap")`; nothing is sent.
6. **Request hash.** `request_hash = sha256(canonical_json({system, messages, schema: output_schema.model_json_schema(), model, effort}))`.
7. **Invoke.** `chat_model.with_structured_output(output_schema, include_raw=True).invoke(messages)`. The result's `raw` is the `AIMessage` (text, `usage_metadata`, `response_metadata`), `parsed` the model instance, `parsing_error` the failure. On a parsing or validation error the gateway appends a user message `"Your previous answer failed validation: <error>. Return only the corrected object."` and invokes again, at most two retries, each attempt recorded as its own `llm_calls` row with `schema_valid` false; the third failure is `GatewayError("schema")`. `stop_reason == "refusal"` (Anthropic) is `GatewayError("refusal")` after recording the row.
8. **Usage and cost.** `usage_metadata.{input_tokens, output_tokens}` and `input_token_details.{cache_read, cache_creation}`; cost from the profile's price table; local and fake providers cost 0. Missing usage (some local servers) is recorded as zeros with `stop_reason="usage_missing"`.
9. **Raw artifact.** `llm_response` JSON `{provider, model, prompt_name, prompt_version, prompt_hash, request_hash, attempt, text, parsed, usage, stop_reason}` stored through the artifact store with producer `{server: "gateway", tool: purpose, version: <gateway version>}`.
10. **Lineage.** One transaction: `llm_calls` row, `artifacts` row for the raw artifact (`producer_step_id = metadata.step_id`), `runs.cost_usd += cost_usd`, event `llm.called` with the row's scalar columns and `raw_ref`. Failures write `llm.failed {kind, message, purpose, provider, model}` in their own transaction before raising.

### 3.3 Providers

`modelforge_gateway.providers` builds a `BaseChatModel` from `(provider, model, effort, settings)`:

- `anthropic`: `ChatAnthropic(model=..., api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url, max_tokens=max_output_tokens, max_retries=2, timeout=...)`. The key comes only from `ANTHROPIC_API_KEY` as read by `load_settings` (so `.env` works); `base_url` is `MODELFORGE_ANTHROPIC_BASE_URL` when set, otherwise the integration's default, and the inherited `ANTHROPIC_BASE_URL` of the parent environment is never consulted. Effort low/medium/high maps to the integration's effort or thinking control; the implementer verifies the parameter spelling against the installed `langchain-anthropic` at task start and uses the model-kwargs passthrough when the integration has no first-class field. If neither path can pass it, the spec is amended and effort is recorded but not sent. Cache read and write tokens come from `usage_metadata.input_token_details`.
- `ollama`: `ChatOllama(model=..., base_url=settings.ollama_base_url, num_predict=max_output_tokens)` with `with_structured_output(schema, method="json_schema")`. Default base URL `http://localhost:11434`.
- `lmstudio`: `ChatOpenAI(model=..., base_url=settings.lmstudio_base_url, api_key="lm-studio", max_tokens=max_output_tokens)` with `with_structured_output(schema, method="json_schema")`. Default base URL `http://localhost:1234/v1`. The only `langchain_openai` import in the repository; it only ever talks to localhost unless the operator points it elsewhere.
- `fake`: section 3.8.

Local model names come from the profile and may be overridden by `MODELFORGE_OLLAMA_MODEL` and `MODELFORGE_LMSTUDIO_MODEL`. Provider switching is never automatic: the profile is fixed at run creation and recorded on `runs.profile`; the CLI `run` rejects an unknown profile or a profile whose provider credential is missing with exit code 2 before creating the run.

### 3.4 Profiles and pricing

`profiles/<name>.yaml`:

```yaml
name: default
routes:
  scope:     {provider: anthropic, model: claude-opus-5-5, effort: low}
  document:  {provider: anthropic, model: claude-opus-5-5, effort: high}
  challenge: {provider: anthropic, model: claude-opus-5-5, effort: high}
  remediate: {provider: anthropic, model: claude-opus-5-5, effort: medium}
  judge:     {provider: anthropic, model: claude-opus-5-5, effort: medium}
prices:   # USD per million tokens: input, output, cache_read, cache_write
  claude-opus-5-5:   {input: 4.0, output: 20.0, cache_read: 0.20, cache_write: 5.0}
  claude-sonnet-5-5: {input: 2.0, output: 10.0, cache_read: 0.20, cache_write: 2.5}
```

Shipped profiles: `default` (above), `ci-cheap` (`claude-sonnet-5-5`, efforts low/medium/medium/low/low), `local-ollama` (`ollama`, `gpt-oss:20b`), `local-lmstudio` (`lmstudio`, `google/gemma-4-26b-a4b-qat`), `fake` (`fake`, model `scripted-v1`). Local and fake models have no price entry and cost 0; an Anthropic model without a price entry is a `config` error at `run` time. `MODELFORGE_PROFILES_DIR` (default `./profiles`) locates the files.

### 3.5 Prompts

`prompts/<agent>/<name>.v<N>.md` with YAML front-matter `purpose`, `version` (equals `N`), `output_schema` (the Pydantic class name), `description`, then the prompt body as a Jinja2 template with `StrictUndefined`. SP3 ships `scope/propose_plan.v1.md`, `document/plan_queries.v1.md`, `document/write_section.v1.md`, and the shared system prompts `scope/system.v1.md`, `document/system.v1.md`.

`PromptRegistry(prompts_dir, lineage)`: `get(name, version) -> PromptRecord{name, version, path, hash, front_matter, template}`; `render(record, **values) -> str`. The first `get` of a record in a process upserts the `prompts` row `(name, version, hash, path)`; an existing row with the same name and version and a different hash raises `GatewayError("config")`: a prompt change is a new version. Every `llm_calls` row carries `prompt_name`, `prompt_version` and `prompt_hash`, so a document can be traced to the exact prompt text.

### 3.6 Redaction

Two layers, as in the umbrella spec's threat model.

**Structural (orchestrator, `excerpts.py`).** Agents never receive artifacts, only excerpts: a per-kind projection that keeps aggregates and drops anything row-level or bulky. Dropped lists are replaced by `{"count": n}` so the citation path `<list>.count` resolves identically on the excerpt and on the artifact (section 6.4). Specifically: `split_manifest` drops `row_ids` (counts kept) and keeps `digests`; `gbm_model` drops `model_text`; `calibration_map` drops the isotonic arrays; `binning_tables` and `scorecard` keep per-feature `iv`, `trend`, `monotonic`, `coefficient` and bin counts but drop the bins; `column_dictionary` keeps `name` and `description` and drops `original`; everything else passes whole (profile, leakage, psi, selection, discrimination, calibration tests, stability, sensitivity, benchmark, validation report, modeling plan). Dataset rows never exist outside Parquet artifacts that no agent reads.

**Scan (gateway).** Before any call, every outbound message is scanned for: an e-mail address; an IBAN-like token (two letters, two digits, eleven to thirty alphanumerics); a run of nine or more digits not adjacent to a hexadecimal character (so 64-hex artifact ids never trip it); each `restricted_terms` entry as a case-insensitive whole-word match. `restricted_terms` is the union of the brief's optional `restricted_terms` list and, once the dataset is loaded, the column dictionary's `original` names; the node supplies them through `CompletionRequest.redaction`. A hit raises `GatewayError("redaction", "<pattern name> in <role> message")` and the run parks; the gateway never masks and never logs the matched text.

### 3.7 Cost and lineage rows

Migration `0003_sp3.sql`: `alter table llm_calls add column seq bigserial, add column ts timestamptz not null default now(); alter table runs add column rerun_llm boolean not null default false;`. `modelforge_lineage.llm_calls` gains `insert_llm_call(conn, LlmCallRecord)` and `list_llm_calls(conn, run_id)` (joined through `steps`, ordered by `seq`); `modelforge_lineage.runs` gains `add_cost(conn, run_id, usd)`, `set_cost_cap(conn, run_id, usd)` and `set_rerun_llm`; `modelforge_lineage.prompts` gains `upsert_prompt`. `RunRecord` gains `rerun_llm`. The run's `cost_usd` in Postgres is the authority for the cap; `RunState.cost_usd` mirrors it for the console and the manifest's reader and is updated by the node from the results it receives (`Step.llm`, section 7).

Default cap: `runs.cost_cap_usd` keeps its column default of 10 USD; `MODELFORGE_COST_CAP_USD` sets the value `run` writes for new runs.

### 3.8 Fake provider

`modelforge_gateway.providers.fake.ScriptedChatModel(BaseChatModel)` answers deterministically from the prompt it receives, costs nothing, and needs no network. It overrides `with_structured_output` to return a runnable producing `{raw, parsed, parsing_error}` like the real integrations. Its behaviour is keyed by the output schema name:

- `ScopeProposal`: parses the fenced `brief` YAML and the `defaults` JSON in the user message and returns the plan SP2's `plan_from_brief` would build (brief fields copied, default methodology, thresholds with overrides), a one-paragraph rationale that cites the first retrieved `[K:]` passage, two assumptions (the proxies) and no open questions.
- `QueryPlan`: zero to three queries derived from the section id (regulatory and methodology sections get queries, data and performance sections get none).
- `SectionDraft`: renders a per-section Markdown script (`fake_sections.yaml`, the SP2 fixture moved into the gateway and rewritten to the alias marker grammar, with `[K:]` markers taken from the passages supplied) by reading values from the fenced `excerpts` JSON block, formatting them with the same 4-significant-figure rule the grounding checker applies, and treating the strings "NaN"/"Infinity" and null as `n/a`.
- Any other schema: a `config` error, so an unscripted purpose fails loudly.

The prompt templates mark the blocks the fake reads with fixed fence info strings (```` ```yaml brief ````, ```` ```json defaults ````, ```` ```json excerpts ````, ```` ```json passages ````); the fake depends on those markers, nothing else in the prompt wording.

Two scripted faults are switchable from the user message for tests: a section id suffixed `#ungrounded` makes the draft contain a number not in the excerpts, and `#refuse` makes the model return an `AIMessage` with `stop_reason="refusal"`.

The fake is the provider of the `fake` profile and the default for every orchestrator test; it is a stand-in, not a recording, and the README says so. Recorded real responses are an SP4 eval concern.

## 4. Knowledge layer

### 4.1 Corpus

`knowledge-base/*.md`, each with front-matter `id, title, source, authority, date, url, kind (primary|summary|internal)`:

| id | kind | content |
|---|---|---|
| `sr11-7` | primary | Federal Reserve SR 11-7 letter and its attachment, verbatim (US government work) |
| `sr26-2` | primary | Federal Reserve SR 26-2 letter, verbatim (US government work) |
| `occ-2026-13` | summary | our summary of the OCC's parallel bulletin, with URL |
| `cecl-asc-326` | summary | our summary of CECL expected credit loss concepts relevant to PD |
| `ifrs9-ecl` | summary | our summary of IFRS 9 staging and lifetime expected credit loss |
| `basel-irb` | summary | our summary of PD, LGD and EAD under the IRB approach |
| `modeling-standards` | internal | required document sections, default thresholds (mirrors `standards.py`), mandatory validation tests, the two dataset proxies |
| `modelforge-governance` | internal | how this system governs itself: gates, four-eyes, lineage, replay, redaction, the SR 26-2 scope framing |

Wording rule, enforced by a test over the corpus and the prompts: SR 26-2 superseded SR 11-7 and SR 21-8 in April 2026 and places generative and agentic AI outside its scope; the text never says SR 26-2 "extends SR 11-7 to LLMs" or that it names LLM-as-judge or adversarial probing. The verbatim letters are fetched once through the browser during implementation, converted to Markdown with headings preserved, and committed; their `url` front-matter points at the Federal Reserve pages.

### 4.2 Chunking and citation ids

Chunks follow headings: each heading (levels 1 to 3) opens a section whose path is the chain of heading titles. Sections longer than about 500 tokens are windowed at 400 words with a 40-word overlap (words are the token proxy; no tokenizer dependency). Citation id: `k_` + the first twelve hex characters of `sha256(doc_id + "\x00" + section_path + "\x00" + chunk_text)`, stable across re-ingestion and unchanged by edits elsewhere in the file.

### 4.3 Index artifact and retrieval

`KnowledgeIndex{index_version: "1", built_from: {doc_id: sha256 of file bytes}, documents: [{id, title, source, authority, date, url, kind}], chunks: [Chunk{citation_id, doc_id, section, text, position}]}` serialised as JSON and stored as a `knowledge_index` artifact. BM25 statistics are rebuilt in memory on load (milliseconds for a few hundred chunks); the artifact pins the corpus, not the scorer.

Retrieval is BM25 (`k1=1.5`, `b=0.75`) over a tokenizer that lowercases, splits on `[a-z0-9]+(?:[-.][a-z0-9]+)*`, drops a fixed English stopword list and applies no stemming. `search(query, k, sources=None) -> list[Hit{citation_id, doc_id, section, text, score}]`, ties broken by `citation_id`, so results are deterministic. No embeddings, no pgvector in SP3 (amendment in section 12); the `Chunk` model leaves room for an `embedding` field and SP4's retrieval-recall eval decides whether a dense channel is worth adding.

### 4.4 `knowledge` server (fifth `ServerName`)

`ServerSettings` gains `knowledge_dir` (`MODELFORGE_KNOWLEDGE_DIR`, default `./knowledge-base`); the orchestrator's `server_environment` passes it. `build()` loads the corpus, builds the index, stores the artifact and keeps the in-memory retriever.

| Tool | Input | Output |
|---|---|---|
| `search_regulation` 1.0.0 | `query: str`, `sources: list[str] \| None`, `k: int = 5` (1 to 20) | `Passages{index_id, hits: list[Passage{citation_id, doc_id, title, section, text, score, url}]}` inline |
| `get_passage` 1.0.0 | `citation_id` | `Passage`; unknown id is `invalid_input` |
| `get_index` 1.0.0 | none | `ArtifactRef` of kind `knowledge_index` |

Contract snapshots cover all three tools; the golden chain adds `get_index` (its id is the corpus pin and changes with any corpus edit, which is the intent). `search_regulation` and `get_passage` return no artifacts, so the `Step` records them as tool calls with the output hash of their JSON.

## 5. Scope agent

`nodes/scope.py`, purpose `scope`, one structured call at the profile's effort for `scope` (low in `default`):

- Inputs: the brief YAML delimited as data in a fenced block, the standards defaults as JSON (`DEFAULT_THRESHOLDS`, `FeaturePolicy()` defaults, the template's section titles) and passages from two fixed `search_regulation` queries against `sources=["modeling-standards"]` ("required validation tests and thresholds for a PD scorecard", "required sections of a model development document"), `k=3` each. The scope step therefore calls the knowledge server before the first gate, and records `knowledge_index` in refs through `get_index`. `get_index` is called on every run, replays included, so the replay compares the corpus pin like any deterministic ref.
- Output `ScopeProposal{plan: ModelingPlan, rationale: str, assumptions: list[str], open_questions: list[str]}`.
- Invariants checked deterministically after the call: `plan.model_name, portfolio, target, intended_use, regulatory_scope, tier, vintage_policy` equal the brief's; `feature_policy.max_features <= brief.feature_policy.max_features`; `feature_policy.exclusions ⊇ brief exclusions`; every threshold in `[0, 1]` and `auc_warn <= auc_min`, `psi_warn <= psi_fail`, `iv_min < iv_max`, `eo_low < 1 < eo_high`; a `[K:]` marker in the rationale must name a retrieved passage. A violation is treated like a schema failure: retried with the violation appended, twice, then `GatewayError("schema")`.
- Stored: `modeling_plan` (kind, shape and `runs.set_plan_ref` unchanged, so `gate_scope`, `seal`, `Manifest.initial_plan_ref` and replay hold) and `scope_notes` (`ScopeNotes{rationale, assumptions, open_questions, passages: list[citation_id]}`, summary `{rationale, assumptions}`). `gate_summary("scope")` adds `rationale` and `assumptions` from that summary.
- Replay: without `rerun_llm` the node copies the original's initial plan and `scope_notes` ref as today; with it, it regenerates.

## 6. Documentation agent

`nodes/document.py`, purpose `document`, two calls per section of `templates/sr11-7-dev-doc/sections.yaml` in order.

### 6.1 Excerpts and aliases

For each section, `excerpts.for_section(section, state, store) -> dict[alias, Excerpt]` where the alias is the `RefName` (`profile`, `discrimination`, `validation`, ...) and `plan` is the current plan (re-stored idempotently through the store to obtain its `ArtifactRef`), selected by the section's `artifact_kinds` through the kind-to-ref map (`split_manifest -> split`, `column_dictionary -> dictionary`, `binning_tables -> binning`, `feature_selection -> selection`, `scorecard -> champion`, `gbm_model -> challenger_model`, `calibration_map -> calibration`, `validation_report -> validation`, `modeling_plan -> plan`, others by name). `manifest` does not exist at document time: the governance section receives the plan excerpt plus a non-citable list of the run's artifact ids and kinds and is instructed to make no numeric claims. A kind whose ref is absent (`psi` without out-of-time vintages) is listed as "not available in this run" so the writer states so.

### 6.2 Calls

1. **Query planning**, `document/plan_queries.v1`: section id, title, guidance, the excerpt aliases with a one-line description each, the plan's `regulatory_scope`. Output `QueryPlan{queries: list[str]}` (0 to 3). Each query runs `search_regulation(k=3)`; hits are deduplicated by `citation_id` and kept in score order.
2. **Writing**, `document/write_section.v1`: guidance, the excerpts as a fenced JSON block keyed by alias, the passages as a fenced JSON block, the marker rules, the number rules (write values to at most four significant figures exactly as given; render "NaN", "Infinity" and null as `n/a`; never invent a number), and the regulatory-sentence rule. Output `SectionDraft{body_md: str, citations: list[str]}`. The body contains no headings; the renderer adds them.

### 6.3 Markers

Drafts cite with aliases: `[A:discrimination#by_split.oot.auc]`, `[K:k_3f9a1c2b7d10]`. The node expands every `A` alias to the artifact id before post-checks and rendering; an unknown alias is an unresolved marker. `DocumentSection.citations` keeps the expanded markers.

### 6.4 Citation path grammar (as-built sharpening of umbrella 4.5)

A path is a sequence of steps separated by `.`. A step applied to an object selects that key. A step applied to a list of objects selects the element whose discriminator equals the step, where the discriminator is `split` for `by_split`, `test_id` for `tests`, `feature` for feature lists (`features`, `entries`, `flags`, `sign_checks`, `monotonicity`, `bin_shifts`, `feature_csi`, `dropped`), `grade` for `grades` and `name` for `columns`; a discriminator that itself contains dots is written in brackets, `tests[disc.auc.oot].value`. The step `count` applied to any list is its length. A path resolves to a JSON scalar, a list of scalars, or fails.

### 6.5 Post-checks and regeneration

`grounding.check_section(body, markers, store, retrieved) -> GroundingReport{ungrounded_numbers, unresolved_markers, uncited_regulatory_sentences, foreign_passages, ok}`:

- **Number grounding.** Numeric tokens are decimal numbers with optional thousands separators and an optional `%` or the word "percent", outside markers, outside inline code, and not part of a regulation name (`SR 11-7`, `SR 26-2`, `SR 21-8`, `OCC 2026-13`, `ASC 326`, `IFRS 9`, `Basel II/III`). A token is grounded when some `[A:]` marker in the same sentence resolves to: a scalar that matches; a list of scalars containing a match; or a string containing the token verbatim (quoting the brief's horizon text). A match means: with the token written to `p` significant figures (`p <= 4`), the value rounded to `p` significant figures equals it; a percent token is compared against `value * 100`; integers compare exactly; "NaN", "Infinity" and null match nothing.
- **Marker validity.** Every `A` marker resolves (alias known, artifact present, path resolves); every `K` marker names a passage retrieved for this section.
- **Regulatory sentences.** A sentence containing a name from the doc-renderer's `REGULATIONS` list carries a `[K:]` marker.

A failing section is regenerated once: both calls run again with the `GroundingReport` appended to the user message. If it still fails, the body is prefixed with the renderer's `REQUIRES AUTHOR ATTENTION` banner and a bullet list of the failures, and the section is stored as written. Nothing is dropped.

### 6.6 Node outputs

`document_sections` (`DocumentSections` with per-section `citations` and a new optional `grounding: GroundingReport`), summary `{n_sections, n_flagged_sections, n_ungrounded_numbers, n_llm_calls}`; then `render_document` and `check_completeness` as in SP2, producing `document_md`, `document_html`, `completeness`. `gate_summary("signoff")` adds `document: refs["document_sections"].summary`. Replay without `rerun_llm` copies the original `document_sections` and re-renders as today; with it, the agent runs again.

## 7. Orchestrator wiring

- `Deps` gains `gateway: Gateway`; the worker builds it in `execute` from the run's recorded profile (`Gateway.for_run(settings, run.profile, Lineage(db), store)`); tests build it with the `fake` profile.
- `Settings` gains `prompts_dir`, `profiles_dir`, `knowledge_dir`, `anthropic_api_key`, `anthropic_base_url`, `ollama_base_url`, `ollama_model`, `lmstudio_base_url`, `lmstudio_model`, `cost_cap_usd` (env `MODELFORGE_PROMPTS_DIR`, `MODELFORGE_PROFILES_DIR`, `MODELFORGE_KNOWLEDGE_DIR`, `ANTHROPIC_API_KEY`, `MODELFORGE_ANTHROPIC_BASE_URL`, `MODELFORGE_OLLAMA_BASE_URL`, `MODELFORGE_OLLAMA_MODEL`, `MODELFORGE_LMSTUDIO_BASE_URL`, `MODELFORGE_LMSTUDIO_MODEL`, `MODELFORGE_COST_CAP_USD`); `.env` fills absent keys as today. `server_environment` passes `MODELFORGE_KNOWLEDGE_DIR`.
- `Step.llm(request) -> CompletionResult` calls `deps.gateway.complete` and accumulates the step's spend in `step.spent_usd`, which the node returns as `cost_usd` in its update (`state.cost_usd + step.spent_usd`); the node builds the request with `metadata=step.call_metadata(section_id=None)`. A `GatewayError` propagates and `Step.__exit__` records it with `kind="llm"` (`_error_record` gains the branch).
- `RunState` gains `rerun_llm: bool = False` (from `runs.rerun_llm` in `initial_state`); `RefName` gains `scope_notes` and `knowledge_index`; `DETERMINISTIC_REFS` gains `knowledge_index`; `ErrorRecord.kind` gains `"llm"`.
- `Brief` gains `restricted_terms: list[str] = []`.
- Replay: `create_replay(conn, run_id, *, rerun_llm)`; `compare_refs(original, replay, *, rerun_llm)` excludes `document_md`, `document_html`, `completeness` when `rerun_llm` and lists them in `ReplayReport.regenerated: list[RefName]`; `ok` ignores regenerated refs. `challenge` and `remediate` keep copying in SP3 whatever the flag says (SP4 extends it).
- CLI: `replay --rerun-llm`; `resume --cost-cap <usd>` (sets `runs.cost_cap_usd` before re-enqueueing; allowed only from `needs_attention`); `status` prints `cost: <usd> / cap <usd>` and `--json` includes `llm_calls`; `run` validates the profile and credentials (section 3.3) and writes `cost_cap_usd` from settings.
- Worker: unchanged flow; `execute` constructs the gateway inside the `tool_client_factory` block so a `config` failure parks the run with a clear error record.

## 8. Docker and CI

The Dockerfile copies `packages/gateway/pyproject.toml` and `packages/knowledge/pyproject.toml` in the dependency layer and `prompts/`, `profiles/`, `knowledge-base/` with the assets; the image sets `MODELFORGE_PROMPTS_DIR`, `MODELFORGE_PROFILES_DIR`, `MODELFORGE_KNOWLEDGE_DIR` and `MODELFORGE_PROFILE=fake` as its default so Compose runs offline until an operator sets a real profile. CI is unchanged apart from the larger test suite: no key, no network beyond the dataset cache.

## 9. Testing

- **Gateway** (no network, fake and constructed-but-never-invoked real models): request hash stable and sensitive to each input; pricing arithmetic per provider; cache token accounting; cap projection parks before sending and `add_cost` is exact after; redaction refusal for each pattern and never in the message text; schema retry twice then `schema`; refusal maps to `refusal`; prompt registry loads, renders, upserts, and raises on a hash conflict; each provider builder returns the expected LangChain class with the expected `base_url` and never reads `ANTHROPIC_BASE_URL` from the environment; `ScriptedChatModel` is deterministic and its `with_structured_output` returns the three-key dict.
- **Knowledge**: citation ids stable under edits elsewhere in a file; chunk windows respect the size and overlap; retrieval on a committed golden question set (`packages/knowledge/tests/golden_questions.yaml`, eight questions, expected `citation_id` in the top 5) passes with recall 1.0; the SR 26-2 wording guard over `knowledge-base/` and `prompts/`; server tools through the in-process MCP client; contract snapshots and goldens for the new server.
- **Grounding**: every grammar rule including brackets and `count`; percent and rounding cases; string and list-of-scalar grounding; regulation-name exclusion; non-finite strings; alias expansion; `[K:]` outside the retrieved set.
- **Nodes** against the fake provider: `scope` writes `modeling_plan`, `scope_notes`, `knowledge_index`, one `llm_calls` row, and parks on a planted invariant violation; `document` writes eleven grounded sections with `n_flagged_sections == 0`, regenerates once and then banners on `#ungrounded`, parks on `#refuse`, and refuses on a planted restricted term; costs accumulate into `RunState.cost_usd` and `runs.cost_usd`; with a test profile that gives the fake model a price entry, a cap of 0.000001 USD parks the run before anything is sent.
- **End to end**: the CLI integration test on the synthetic brief under `MODELFORGE_PROFILE=fake` (fast; every stage, replay, `--rerun-llm` with `regenerated` populated and new `llm_calls` rows, verify); the Polish brief (slow); import-linter contracts (`make lint`); the no-pickle scan over six packages.

## 10. Deferred and out of scope

- `challenge` and `remediate` agents, the challenger isolation sentinel test, findings and responses through the gateway (SP4).
- Eval harness layers, LLM judge, recorded responses, baselines, `evals.yml` (SP4).
- Dense retrieval and pgvector use, rerankers (SP4 decides from the retrieval eval).
- OpenTelemetry spans for LLM calls, trace ids (SP5); the console's citation hover and grounding indicator (SP5).
- Streaming output; LangSmith tracing (works through LangChain's environment variables, not exercised).
- SP2's deferred minors stay deferred unless one blocks a task.

## 11. Risks

- **Integration lag.** `langchain-anthropic` may not expose effort or adaptive thinking first-class; the fallback is model-kwargs passthrough, then an amendment. Verified at the first gateway task.
- **Local model quality.** `gpt-oss:20b` and `gemma-4-26b` may fail structured output or grounding more often than Claude; the run parks or sections carry banners, and the README records the measured outcome. That is a result, not a defect.
- **Grounding strictness.** Real models write numbers in forms the rule rejects ("about 87 percent"); the prompt is explicit and the regeneration pass carries the failure list. Measured in the Polish live runs.
- **Verbatim corpus.** The Federal Reserve pages may be PDFs with imperfect text extraction; the Markdown is checked by eye for heading structure before commit.
- **Token budget per run.** Eleven sections with excerpts of a few kilobytes each is roughly 200k input tokens with Opus 5.5, about 1 USD per run, inside the 10 USD cap with headroom for retries.

## 12. Umbrella spec amendments to record (closing docs commit)

- 7.1: the gateway is a class with `complete()`, not a Runnable with a callback handler; structured output via `with_structured_output(include_raw=True)`; no streaming.
- 7.5: redaction specifics (section 3.6) and `restricted_terms` on the brief.
- 8.2: ref aliases in markers, expanded by the node; the two-call pattern unchanged.
- 4.5 (SP2 ruling) and 6.4 here: bracketed discriminators and `count`; number matching by the token's own precision; string and list-of-scalar grounding.
- 9: the knowledge index is a content-addressed artifact pinned in every manifest; BM25 only; pgvector unused until a later sub-project.
- 10.1: `llm_calls.seq`, `llm_calls.ts`, `runs.rerun_llm`.
- 17 row 3: as built, plus `scope_notes` and `knowledge_index` refs.
