This page is the reference for the LLM gateway; the README is the front door.

# LLM gateway

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

[Back to the README](../../README.md)
