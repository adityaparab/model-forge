# ModelForge

ModelForge is a governed, multi-agent copilot for the credit risk model development lifecycle. Given a raw credit dataset and a modeling brief, it produces a validated probability-of-default (PD) model, an SR 11-7 style model development document, an independent "effective challenge" review, a tamper-evident audit trail, and a model inventory entry, with human approval gates at the points where a bank would require them.

The core toolkit (`packages/core`, `modelforge_core`) is the deterministic, LLM-free layer underneath. It loads a dataset into a canonical schema, profiles it and screens for leakage, builds train / validation / out-of-time splits, fits monotone WoE binning and IV / correlation feature selection, trains a WoE logistic scorecard champion and a LightGBM challenger, calibrates the champion, and validates it: discrimination (AUC, KS, Gini with bootstrap intervals), calibration (per-grade binomial, Hosmer-Lemeshow, expected / observed), stability (PSI, CSI), sensitivity (coefficient signs, WoE monotonicity, bin shifts), and a champion-challenger benchmark. The champion is refit iteratively, dropping any feature whose coefficient has the wrong sign, and these drops are recorded in the feature selection artifact. Every result is checked against documented thresholds into a single validation report. Models are stored as JSON (scorecard) and LightGBM native text (challenger); nothing is pickled.

## Quick start

```bash
uv sync --all-groups
make lint test   # ruff, pyright, import-linter, then the full suite (slow tests use real data)
make example     # end-to-end run on the Polish data, out-of-time vintage 5
```

`make example` downloads the dataset into `data/` on first use, prints one line per validation test plus the overall status, and writes `scorecard.json`, `gbm_model.txt`, `validation_report.json`, `binning_tables.json` and `column_dictionary.json` under `out/polish_bankruptcy/`. For the second dataset:

```bash
uv run python examples/core_pipeline.py --dataset taiwan_bankruptcy --oot-vintages ""
```

Other flags: `--data-dir` (default `data`), `--out` (default `out`), `--seed` (default 42), `--max-features` (default 20).

## Commands

```bash
make lint       # ruff, pyright, import-linter
make test       # full test suite
make test-fast  # skip tests marked slow
make example    # end-to-end example pipeline
```

## Datasets

- Polish companies bankruptcy data, UCI Machine Learning Repository, DOI 10.24432/C5F600, licensed CC BY 4.0.
- Taiwanese bankruptcy prediction, UCI Machine Learning Repository.

Both are corporate bankruptcy datasets, not retail credit data, so two proxies are documented and applied:

- **Bankruptcy as default.** The bankruptcy label stands in for the default flag of a PD model.
- **Horizon files as vintages.** The Polish data ships as five files by forecasting horizon (`1year` to `5year`); each file is treated as a vintage, so a held-out file gives an out-of-time split. The Taiwanese set has no time dimension at all, so it uses a stratified random split and every out-of-time test is reported as not applicable.

## Status

Sub-project 1 (core toolkit) is complete. See the architecture spec at `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` and the implementation plan at `docs/superpowers/plans/2026-11-09-sp1-core-toolkit.md`.
