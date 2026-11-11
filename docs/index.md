# Documentation

ModelForge is a reference implementation, not a production system. This page is the map of its documentation: read the first six in order for the whole picture in under two hours, then use the reference pages for detail.

## Reading order

| Order | Document | Minutes | What it is for |
| --- | --- | --- | --- |
| 1 | [README](../README.md) | 10 | What ModelForge is, how to run the demo, how it is built, and what was measured. |
| 2 | [Walkthrough](walkthrough.md) | 10 | The demo run step by step, one screenshot each, with where the same thing is in the API. |
| 3 | [Design memo](design-memo.md) | 30 | For a model-risk reviewer: the model, the agents around it, the controls and the evidence that proves each one. |
| 4 | [Domain primer](domain-primer.md) | 30 | For an engineer: PD models, scorecards, validation and model risk management, with a glossary. |
| 5 | [Threat model](threat-model.md) | 15 | Trust boundaries, the threats with their mitigations as built, and the residual risks. |
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

- [Specs](superpowers/specs/): the architecture design and one design per sub-project.
- [Plans](superpowers/plans/): the task-by-task implementation plans.
- [Notes](superpowers/notes/): the hand-off note written at the end of each sub-project.
