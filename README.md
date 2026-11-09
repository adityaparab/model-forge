# ModelForge

ModelForge is a governed, multi-agent copilot for the credit risk model development lifecycle. Given a raw credit dataset and a modeling brief, it produces a validated probability-of-default (PD) model, an SR 11-7 style model development document, an independent "effective challenge" review, a tamper-evident audit trail, and a model inventory entry, with human approval gates at the points where a bank would require them.

Status: sub-project 1 in progress.

## Commands

```bash
make lint       # ruff, pyright, import-linter
make test       # full test suite
make test-fast  # skip tests marked slow
```
