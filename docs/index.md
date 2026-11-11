# Documentation

ModelForge is a reference implementation, not a production system. This page is the map of its documentation: read the first six in order for the whole picture in under two hours, then use the reference pages for detail.

## Reading order

| Order | Document | Minutes | What it is for |
| --- | --- | --- | --- |
| 1 | [README](../README.md) | 10 | What ModelForge is, how to run the demo, how it is built, and what was measured. |
| 2 | [Walkthrough](walkthrough.md) | 10 | The demo run step by step, one screenshot each, with where the same thing is in the API. |
| 3 | [Design memo](design-memo.md) | 25 | For a model-risk reviewer: the model, the agents around it, the controls and the evidence that proves each one. |
| 4 | [Domain primer](domain-primer.md) | 25 | For an engineer: PD models, scorecards, validation and model risk management, with a glossary. |
| 5 | [Threat model](threat-model.md) | 10 | Trust boundaries, the threats with their mitigations as built, and the residual risks. |
| 6 | [Trade-offs](trade-offs.md) | 10 | Where a model is used and where it is not, what each choice cost in practice, and the hosted-to-local gap. |

The measured runs behind every published number are in [`results/`](results/README.md).

## Reference

| Page | Covers |
| --- | --- |
| [Run a governed model build](reference/run.md) | The CLI run, the gates and their decisions, statuses, pause, replay and verify, the API path, identity and roles, server-side settings and costs. |
| [Console](reference/console.md) | The console's pages and what each shows. |
| [API](reference/api.md) | The HTTP endpoints, their roles and their errors. |
| [Tracing](reference/tracing.md) | OpenTelemetry traces to Jaeger and Phoenix. |
| [MCP servers](reference/mcp-servers.md) | The servers and the tools they expose. |
| [LLM gateway](reference/gateway.md) | Routing, schema-checked output, prompts, cost accounting and redaction. |
| [Knowledge layer](reference/knowledge.md) | The corpus, retrieval and citation markers. |
| [Challenger review](reference/challenger.md) | What the challenger sees, its pre-checks, findings and evidence grammar. |
| [Remediation loop](reference/remediation.md) | How findings are answered and the loop is bounded. |
| [Evals](reference/evals.md) | The eval harness, its layers, profiles, baselines and CI. |
| [Running against a real model](reference/live-models.md) | Anthropic, Ollama and LM Studio profiles and earlier live runs. |
| [Compose](reference/compose.md) | The Docker Compose stack and `make demo`. |
| [Configuration](reference/configuration.md) | Environment variables and profiles. |
| [Commands](reference/commands.md) | The make targets and CLI commands. |
| [Datasets](reference/datasets.md) | The two datasets, their licenses and the proxies applied. |

## How it was built

ModelForge was built as six sub-projects, each with a design spec, an implementation plan and a hand-off note to the next: the core toolkit, the orchestrator and lineage store, the gateway with the knowledge layer and agents, the challenger with remediation and evals, the API with the console, and this documentation.

The architecture design is the umbrella; its section 17.1 is the first sub-project's scope, and its sections 20 to 23 (SP3 to SP6) record what each later sub-project changed as built.

| Sub-project | Design | Plan | Hand-off note |
| --- | --- | --- | --- |
| Architecture | [Architecture design](superpowers/specs/2026-11-09-modelforge-architecture-design.md) | | |
| 1. Core toolkit | Section 17.1 of the architecture design | [Plan](superpowers/plans/2026-11-09-sp1-core-toolkit.md) | [Hand-off](superpowers/notes/2026-11-09-sp1-handoff.md) |
| 2. Orchestrator and lineage | [Design](superpowers/specs/2026-11-09-sp2-orchestrator-design.md) | [Plan](superpowers/plans/2026-11-09-sp2-orchestrator.md) | [Hand-off](superpowers/notes/2026-11-09-sp2-handoff.md) |
| 3. Gateway, knowledge and agents | [Design](superpowers/specs/2026-11-09-sp3-gateway-knowledge-agents-design.md) | [Plan](superpowers/plans/2026-11-09-sp3-gateway-knowledge-agents.md) | [Hand-off](superpowers/notes/2026-11-09-sp3-handoff.md) |
| 4. Challenger, remediation and evals | [Design](superpowers/specs/2026-11-10-sp4-challenger-remediation-evals-design.md) | [Plan](superpowers/plans/2026-11-10-sp4-challenger-remediation-evals.md) | [Hand-off](superpowers/notes/2026-11-10-sp4-handoff.md) |
| 5. API and console | [Design](superpowers/specs/2026-11-10-sp5-api-console-design.md) | [Plan](superpowers/plans/2026-11-10-sp5-api-console.md) | [Hand-off](superpowers/notes/2026-11-10-sp5-handoff.md) |
| 6. Documentation and measurements | [Design](superpowers/specs/2026-11-11-sp6-docs-presentation-design.md) | [Plan](superpowers/plans/2026-11-11-sp6-docs-presentation.md) | [Hand-off](superpowers/notes/2026-11-11-sp6-handoff.md) |
