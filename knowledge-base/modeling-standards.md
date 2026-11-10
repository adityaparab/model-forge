---
id: modeling-standards
title: "ModelForge Modeling Standards: thresholds, validation tests, document sections, methodology and data"
source: "ModelForge project; Thresholds defaults in modelforge_core.schemas, tests in modelforge_core.thresholds, sections in templates/sr11-7-dev-doc/sections.yaml"
authority: "ModelForge project (internal standard)"
date: "2026-11-10"
url: "knowledge-base/modeling-standards.md"
kind: internal
note: "Internal standard. The numbers below must match Thresholds() in modelforge_core.schemas; the url is the repository path because this document has no external home."
---

# ModelForge Modeling Standards

The standards every probability-of-default (PD) model built with this system is held to. A modeling plan may request other thresholds; otherwise these defaults apply.

## Default thresholds

The default thresholds are the values of `Thresholds()`:

| Key | Default | Meaning |
|---|---|---|
| `auc_min` | 0.70 | AUC at or above this passes |
| `auc_warn` | 0.65 | AUC at or above this, but below `auc_min`, warns; lower fails |
| `ks_min` | 0.25 | out-of-time KS at or above this passes; lower fails |
| `gap_max` | 0.05 | train to out-of-time AUC gap at or below this passes |
| `gap_fail` | 0.10 | gap at or below this, but above `gap_max`, warns; higher fails |
| `psi_warn` | 0.10 | PSI or CSI below this passes |
| `psi_fail` | 0.25 | PSI or CSI below this, but at or above `psi_warn`, warns; at or above it fails |
| `calibration_alpha` | 0.05 | significance level of the per-grade binomial test |
| `grade_fail_share_warn` | 0.20 | if any grade rejects the binomial test, a rejecting share at or below this warns; above it fails |
| `hl_p_min` | 0.05 | Hosmer-Lemeshow p-value at or above this passes |
| `eo_low` | 0.80 | lower bound of the passing expected to observed ratio |
| `eo_high` | 1.25 | upper bound of the passing expected to observed ratio |
| `iv_min` | 0.02 | features with information value below this are dropped |
| `iv_max` | 0.50 | features with information value above this are dropped as suspicious |
| `corr_max` | 0.70 | absolute WoE correlation above this prunes the weaker feature |
| `benchmark_gap` | 0.03 | challenger AUC minus champion AUC at or below this passes |

## Mandatory validation tests

Every model is validated by the same thirteen tests. Each returns pass, warn, fail or not applicable, and the overall result is the worst status among the applicable tests.

| `test_id` | Name | Rule with default thresholds |
|---|---|---|
| `disc.auc.oot` | Out-of-time AUC | >= 0.70 pass, >= 0.65 warn, else fail |
| `disc.auc.val` | Validation AUC | >= 0.70 pass, >= 0.65 warn, else fail |
| `disc.ks.oot` | Out-of-time KS | >= 0.25 pass, else fail |
| `disc.gap.train_oot` | Train to out-of-time AUC gap | <= 0.05 pass, <= 0.10 warn, else fail |
| `cal.binomial` | Per-grade binomial test | share of grades with p < 0.05: 0 pass, <= 0.20 warn, else fail |
| `cal.hl` | Hosmer-Lemeshow test | p >= 0.05 pass, else warn |
| `cal.eo` | Expected to observed ratio | 0.80 <= E/O <= 1.25 pass, else warn |
| `stab.psi.score` | Score PSI | < 0.10 pass, < 0.25 warn, else fail |
| `stab.csi.max` | Maximum feature CSI | < 0.10 pass, < 0.25 warn, else fail |
| `sens.signs` | Coefficient signs | 0 wrong signs pass, else fail |
| `sens.monotonic` | WoE monotonicity | 0 non-monotone pass, else warn |
| `bench.gap` | Champion versus challenger AUC gap | challenger - champion AUC <= 0.03 pass, else warn |
| `feat.iv_range` | Maximum selected-feature IV | <= 0.50 pass, else fail |

The out-of-time tests (`disc.auc.oot`, `disc.ks.oot`, `disc.gap.train_oot`) are reported as not applicable when the dataset has no time dimension. Calibration tests use ten PD-quantile grades by default.

## Required document sections

The development document has eleven sections, in this order:

1. Executive summary
2. Model purpose and intended use
3. Regulatory and policy context
4. Data
5. Methodology and conceptual soundness
6. Model development
7. Performance and validation results
8. Limitations and weaknesses
9. Ongoing monitoring plan
10. Governance and lineage
11. Appendices

## Methodology: champion and challenger

The champion is a weight-of-evidence (WoE) logistic regression scorecard. Features are binned and each bin gets a WoE value. Constant features and features outside the `iv_min` to `iv_max` range are dropped, then a feature whose WoE correlation with a stronger kept feature exceeds `corr_max` is pruned. A logistic regression on the WoE features gives a scorecard with points per bin, calibrated to PDs and stored as JSON.

The challenger is a LightGBM gradient-boosted tree model trained on the same splits and stored in LightGBM's native text format. It benchmarks how much discrimination a flexible model can find: if it beats the champion's AUC by more than `benchmark_gap`, `bench.gap` warns. The champion stays the model of record because its coefficients, signs and bins can be explained and challenged.

## Datasets and proxies

The reference datasets are the Polish companies bankruptcy data and the Taiwanese bankruptcy prediction data, both from the UCI Machine Learning Repository. Both are corporate bankruptcy data, not retail credit data, so two proxies apply and are disclosed in the Data and Limitations sections:

- **Bankruptcy as default.** The bankruptcy label stands in for the default flag of a PD model.
- **Horizon files as vintages.** The Polish data ships as five files by forecasting horizon (`1year` to `5year`). Each file is treated as a vintage, so a held-out file gives an out-of-time split. The Taiwanese data has no time dimension, so it uses a stratified random split and every out-of-time test is reported as not applicable.
