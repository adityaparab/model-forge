# Domain primer

This primer is for an engineer who reads the ModelForge code without a background in credit risk. For each concept it says what the concept is, why a bank cares about it, and where it lives in this repository: the `core` module, the MCP tool and the console tab. It takes about thirty minutes. ModelForge is a reference implementation, not a production system, and the terms here are explained at the depth the code needs, not at the depth a credit-risk examination would.

The [design memo](design-memo.md) argues the design for a model-risk reviewer and the [README](../README.md) shows how to run it; this page supplies the vocabulary both assume. The [glossary](#glossary) at the end collects every term.

## What a PD model is and who uses it

A **probability-of-default (PD) model** estimates, for each borrower, the probability that the borrower defaults within a fixed horizon, usually one year. A borrower in this vocabulary is an **obligor**; the obligor can be a person (a retail model) or a company (a wholesale model). "Default" has a precise definition in each bank, typically ninety days past due on a material obligation or an assessment that the obligor is unlikely to pay. The model's output is a number between zero and one per obligor, and that number is used in several places:

- **Origination.** A credit officer, or an automated decision engine, uses the PD to approve or decline an application, set a limit and price the loan. A higher PD means a higher price or a smaller limit.
- **Portfolio management.** Risk appetite statements set limits on how much exposure a bank holds in each PD band, so the PD drives concentration reporting and portfolio steering.
- **Capital.** Under the Basel internal-ratings-based (IRB) approach, a bank that has supervisory permission estimates PD itself and feeds it into the capital formula that decides how much capital it must hold against each exposure.
- **Provisioning.** Accounting standards for expected credit losses, IFRS 9 and the US CECL standard, require a bank to set aside an allowance for losses it expects. The usual building block is expected loss: PD times LGD times EAD (all defined below).

Because one PD feeds pricing, capital and the balance sheet, an error in it is expensive in several directions at once. That is why a PD model is the canonical example of a model that model risk management governs, and why ModelForge builds one.

In this repository the model is a PD scorecard on corporate data: the champion is a weight-of-evidence logistic regression, its benchmark is a LightGBM challenger, and both are fitted by [`packages/core`](../packages/core/modelforge_core/). The language-model agents around them are tooling; the [design memo](design-memo.md) explains why that split decides what gets validated.

## The IRB vocabulary and the proxies this project uses

The Basel IRB approach names three risk parameters. Every credit model in a bank estimates one of them.

- **PD, probability of default.** The chance the obligor defaults within the horizon. This project builds PD only.
- **LGD, loss given default.** The share of the exposure the bank loses if the obligor defaults, after collateral and recoveries.
- **EAD, exposure at default.** How much the obligor owes at the moment of default, including undrawn limits it may draw before defaulting.

Two further terms describe how a credit model is tested in time.

- **Vintage.** A cohort of obligors grouped by the period their observation starts, for example all loans booked in one quarter. Credit models are built on older vintages and must work on newer ones, because the economy, the bank's underwriting and the population all drift.
- **Out-of-time (OOT).** A holdout sample from a later period than the training data. A random holdout from the same period flatters the model, because it shares the period's conditions with the training sample; an out-of-time sample is the honest test, and model-risk reviewers ask for it first.

ModelForge ships with public corporate bankruptcy data rather than a bank's loan book, so two proxies stand in for the real definitions. Both are written into the example brief and the challenger is expected to raise them.

- **Bankruptcy as default.** The dataset's bankruptcy label is the target. A real default definition is broader and earlier than bankruptcy.
- **Horizon files as vintages.** The Polish companies dataset comes as five files by forecasting horizon, from one to five years before the bankruptcy outcome. Each file is treated as a vintage, and the brief [`examples/briefs/polish-pd.yaml`](../examples/briefs/polish-pd.yaml) holds out the fifth file as the out-of-time sample. That sample is a different horizon, not a later calendar period, so the out-of-time tests are a proxy for the real thing. The Taiwanese dataset has no time dimension at all; it uses a stratified random split and every out-of-time test reports itself as not applicable.

The [datasets reference](reference/datasets.md) lists both datasets with their licences. In code the target is always `default_flag` and the key `obligor_id`, whatever the source calls them.

## How a scorecard is built

A **scorecard** is a logistic regression presented as a table of points: each feature is cut into bins, each bin carries a number of points, and an obligor's score is the sum of the points of the bins it falls into. Banks favour scorecards because a credit officer, an auditor and a regulator can each read one line by line. The run's `profile`, `split`, `features` and `build` nodes build one through the `data-profiler` and `credit-modeling` MCP servers; the [MCP servers reference](reference/mcp-servers.md) lists them.

### Profiling and leakage screening

Before modeling, the data are profiled: per-feature summary statistics, missing shares and the target rate by vintage ([`profiling.py`](../packages/core/modelforge_core/profiling.py), tool `profile_dataset`). A default rate that moves across vintages is the first sign that the out-of-time sample will behave differently.

**Leakage** is a feature that contains the answer: a field filled in after the default, a recovery amount, an identifier correlated with the outcome. A model with leakage looks excellent in development and fails in production. [`leakage.py`](../packages/core/modelforge_core/leakage.py) (tool `check_leakage`) flags features that separate the classes almost perfectly on their own, constant features and identifier-like columns, and the `features` node drops every flagged feature before binning.

### Splits

[`splits.py`](../packages/core/modelforge_core/splits.py) (tool `make_splits`) builds three samples: **train** for fitting, **validation** for calibration and model choice, and **out-of-time** for the honest test. With the vintage strategy, the brief's out-of-time vintages are held out and the rest is split into train and validation, stratified by the target and seeded. The split is recorded as a manifest of obligor ids with a hash, so a replay uses exactly the same rows.

### Binning and weight of evidence

Each candidate feature is cut into a small number of **bins** on the training sample ([`binning.py`](../packages/core/modelforge_core/binning.py), tool `bin_features_woe`). The bins start from quantiles, small bins are merged into their neighbours, and by default adjacent bins are merged until the default rate moves in one direction across them. That **monotonicity** matters to a bank: a scorecard that says risk falls as profitability rises, then rises again, then falls, is hard to defend to a reviewer unless the business reason is known. Missing values get their own bin.

Each bin then gets a **weight of evidence (WoE)**: the natural log of the bin's share of good obligors over its share of bad obligors. A positive WoE means the bin holds relatively more good obligors than the population, so in this codebase a higher WoE means lower risk. Replacing each raw value with its bin's WoE puts every feature on the same log-odds scale, handles outliers and missing values without imputation, and makes a linear model on top of it behave sensibly.

### Information value and selection

The **information value (IV)** of a feature sums, over its bins, the difference between the good and bad shares times the WoE. It measures how well the binned feature separates goods from bads on its own. Rules of thumb treat an IV below about 0.02 as useless and an IV above about 0.5 as suspicious: a feature that predicts that well on its own is more often leakage than insight.

[`selection.py`](../packages/core/modelforge_core/selection.py) (tool `select_features`) drops constant features, features with IV below the plan's `iv_min` and above its `iv_max`, then walks the survivors in descending IV order and drops any feature whose WoE correlation with an already kept feature exceeds `corr_max`, up to an optional cap on the count. Correlated features split their effect between them and can push one coefficient to the wrong sign.

### The logistic fit and the points

[`logistic.py`](../packages/core/modelforge_core/logistic.py) (tool `fit_logistic_pd`) fits a logistic regression of the default flag on the WoE-transformed features of the training sample. Because a higher WoE means lower risk, every coefficient should be negative. A coefficient of zero or above is a **wrong sign**, usually caused by correlation with another feature, so the default stepwise fit drops the feature with the largest non-negative coefficient and refits until every sign is right.

The fitted log-odds are then turned into **points**. The scaling has three parameters: a base score, the odds at that base score, and the **points to double the odds (PDO)**. The defaults in [`schemas.py`](../packages/core/modelforge_core/schemas.py) are a base score of 600 at good-to-bad odds of 50 to 1, with 20 points doubling the odds. The intercept is spread evenly over the features, so each bin carries its own whole number of points, and a higher score means a lower PD. [`scorecard.py`](../packages/core/modelforge_core/scorecard.py) holds the lookup and the JSON serialisation: the JSON scorecard is the model, and no model is ever pickled.

### The challenger and calibration

Next to the champion, [`gbm.py`](../packages/core/modelforge_core/gbm.py) (tool `fit_gbm_challenger`) fits a LightGBM gradient-boosted model on the same selected features, stored in LightGBM's own text format. It is not a candidate for use: it is a **benchmark** that shows how much predictive power the simpler, explainable scorecard leaves on the table.

A model can rank obligors well and still state the wrong level of risk: it can say two percent where five percent default. **Calibration** maps the model's raw output to a PD that matches observed default rates. [`calibration.py`](../packages/core/modelforge_core/calibration.py) (tool `calibrate`) supports Platt scaling (a one-feature logistic fit on the raw score), isotonic regression and identity; the `build` node uses Platt scaling fitted on the validation sample. [`scoring.py`](../packages/core/modelforge_core/scoring.py) (tool `score`) then scores every obligor in every split with a raw score and a PD, which is the frame every validation test reads.

In the console, the artifacts from these steps are listed on the **Lineage** tab of a run, each with its hash; the binning, the selection and the scorecard reach the human at the model gate through the **Validation** tab.

## How it is validated

**Validation** asks whether the model works and whether its weaknesses are known. A bank's validation function looks at conceptual soundness, data quality, performance and the outcome of ongoing monitoring; ModelForge automates the performance part as thirteen deterministic tests. The `validate` node calls the `validation` MCP server's tools (`discrimination_metrics`, `calibration_tests`, `stability_tests`, `sensitivity_analysis`, `benchmark_models`) and then `evaluate_thresholds`, which [`thresholds.py`](../packages/core/modelforge_core/thresholds.py) implements. That module recomputes every status from the raw values and the plan's thresholds, so the thresholds are the single authority for every verdict. A test is **pass**, **warn** or **fail**, or **not applicable** when its input does not exist (no out-of-time sample, for example), and the report's overall band is the worst applicable status.

The thresholds below are the defaults of `Thresholds` in [`schemas.py`](../packages/core/modelforge_core/schemas.py). A brief can override them, the scope agent's plan carries them, and the human approves the plan at the scope gate, so the thresholds in force are a recorded decision rather than a constant. The tests evaluate on the out-of-time sample when there is one and on the validation sample otherwise. The **Validation** tab of a run shows all thirteen with their values, thresholds and messages.

### Discrimination

**Discrimination** is the model's ability to rank bad obligors above good ones. [`metrics.py`](../packages/core/modelforge_core/metrics.py) computes per split:

- **AUC**, the area under the ROC curve: the probability that a randomly chosen defaulter gets a higher PD than a randomly chosen non-defaulter. 0.5 is a coin toss and 1.0 is perfect.
- **Gini**, equal to 2 AUC minus 1; credit teams often quote Gini rather than AUC.
- **KS**, the Kolmogorov-Smirnov statistic: the largest gap between the cumulative score distributions of goods and bads.

Each comes with a seeded bootstrap confidence interval.

| Test | Measures | Default threshold | Why a bank cares |
| --- | --- | --- | --- |
| `disc.auc.val` | AUC on validation | pass at 0.70 or above, warn at 0.65 or above, else fail | The model must rank well on data it was not fitted on. |
| `disc.auc.oot` | AUC out of time | as above | Ranking must survive a move to another period. |
| `disc.ks.oot` | KS out of time | pass at 0.25 or above, else fail | A second view of separation, common in credit reporting. |
| `disc.gap.train_oot` | train AUC minus out-of-time AUC | pass at 0.05 or below, warn at 0.10 or below, else fail | A large drop from train to out-of-time signals overfitting or drift. |

### Calibration

**Calibration** tests whether the PD level is right, which matters most for capital and provisioning, where the PD is used as a number rather than as a rank. [`calibration_tests.py`](../packages/core/modelforge_core/calibration_tests.py) groups obligors into **grades** by PD (the rating grades a bank would report) and compares predicted defaults with observed defaults.

| Test | Measures | Default threshold | Why a bank cares |
| --- | --- | --- | --- |
| `cal.binomial` | per grade, a binomial test of observed defaults against the grade's PD; the share of grades that reject at the 0.05 level | no grade rejects to pass, at most a 0.20 share to warn, else fail | Each grade's PD should be believable on its own. |
| `cal.hl` | the Hosmer-Lemeshow test across grades | p-value of 0.05 or above to pass, else warn | A single goodness-of-fit view across all grades. |
| `cal.eo` | expected over observed defaults in total | ratio between 0.80 and 1.25 to pass, else warn | Too low understates losses and capital; too high overprices and over-provisions. |

### Stability

**Stability** asks whether the population the model is applied to still looks like the one it was built on. [`stability.py`](../packages/core/modelforge_core/stability.py), using [`psi.py`](../packages/core/modelforge_core/psi.py), compares the training sample with the evaluation sample.

- The **population stability index (PSI)** buckets the base sample's PDs, then sums, over the buckets, the difference in shares times the log of their ratio. It is zero when the distributions match and grows with the shift.
- The **characteristic stability index (CSI)** is the same calculation applied to one input feature at a time, which tells you which feature moved.

| Test | Measures | Default threshold | Why a bank cares |
| --- | --- | --- | --- |
| `stab.psi.score` | PSI of the PD distribution | below 0.10 pass, below 0.25 warn, else fail | A shifted score distribution means the model sees a different population than it was built for. |
| `stab.csi.max` | the largest CSI among the selected features | same bands | Points to the feature driving any shift. |

### Sensitivity

**Sensitivity** checks that the model responds to its inputs the way the business expects. [`sensitivity.py`](../packages/core/modelforge_core/sensitivity.py) checks each coefficient's sign and each feature's WoE trend, and also records how much the PD moves when each feature is shifted by one bin, which the documentation agent can cite.

| Test | Measures | Default threshold | Why a bank cares |
| --- | --- | --- | --- |
| `sens.signs` | coefficients with the wrong sign | none to pass, else fail | A wrong sign means the model rewards risk; a reviewer will not accept it. |
| `sens.monotonic` | selected features whose WoE has no monotone trend | none to pass, else warn | Non-monotone bins need a business explanation. |

### Benchmark and feature range

| Test | Measures | Default threshold | Why a bank cares |
| --- | --- | --- | --- |
| `bench.gap` | challenger AUC minus champion AUC, on the out-of-time sample (else validation) | 0.03 or below to pass, else warn | If the black-box challenger is much better, the scorecard leaves value on the table and the choice must be justified. |
| `feat.iv_range` | the highest IV among selected features | 0.50 or below to pass, else fail | A feature that predicts too well on its own is likely leakage. |

[`benchmark.py`](../packages/core/modelforge_core/benchmark.py) computes the gap with a paired bootstrap confidence interval, so the reviewer can see whether it is noise.

### What the tests do not cover

The thirteen tests cover performance. They do not judge whether the data are fit for the intended use, whether the proxies are acceptable, or whether the model's limitations are stated; those are **conceptual soundness** questions, and in ModelForge they fall to the development document, the challenger and the humans at the gates. A test that passes is evidence, not approval.

## What model risk management does

**Model risk** is the risk of loss from decisions based on a model that is wrong or misused. **Model risk management (MRM)** is the function that controls it. In the usual three-lines arrangement, model developers and users are the first line, MRM is the second line that validates and approves, and internal audit is the third line that checks the whole process. MRM's work falls into five activities. Each has a counterpart in ModelForge, and for each the console shows where.

### Inventory

The **model inventory** is the register of every model the bank uses: its purpose, owner, status, risk tier, validation history and next review date. A model that is not in the inventory is not governed. The `intake` node registers a stub entry for the run's model, and `seal` finalises it with the champion, the validation summary and any sign-off conditions ([`inventory.py`](../packages/lineage/modelforge_lineage/inventory.py)). The console's **Inventory** page lists the entries with links to their runs. A real inventory process (tiering, ownership, scheduled reviews, retirement) is one of the things the [design memo](design-memo.md) lists for an adopter to add.

### Validation

Validation is the independent review of a model before use and periodically after it. The deterministic part, the thirteen tests above, is the `validate` node, and its report is on the **Validation** tab. The development document that a validator reads first is written by the `document` node against a template derived from SR 11-7's documentation expectations, and every number in it is checked against the artifacts; it is on the **Document** tab, where each cited value resolves to its artifact and each regulatory citation to its passage.

### Effective challenge

**Effective challenge** is critical analysis by people who have the competence to find a model's weaknesses, the independence to report them and the influence to get them fixed. ModelForge's `challenge` node runs deterministic completeness pre-checks and then a reviewing agent that sees the rendered document, the validation results, the recorded plan and its own retrieved passages, never the developer's brief, notes or drafts ([`challenger.py`](../packages/orchestrator/modelforge_orchestrator/challenger.py)). Its findings, with severities and evidence, and the `remediate` node's responses to them are on the **Findings** tab, grouped by the round that raised them. The challenger is an aid to effective challenge, not a substitute for an independent validation function; the [challenger reference](reference/challenger.md) has the detail.

### Approval

A model goes into use only when an accountable person approves it, and the person who built or requested it may not be that person: the **four-eyes** rule. ModelForge stops at three gates: `gate_scope` (approve the plan), `gate_model` (accept the fitted model) and `gate_signoff` (approve, approve with conditions, or reject the whole package). The model and sign-off decisions must come from someone other than the requester, and an unconditional approval is refused while a critical or high finding is open ([`approvals.py`](../packages/lineage/modelforge_lineage/approvals.py)). The gate panel and the list of approvals are on the **Overview** tab of a run.

### Monitoring

After approval, MRM requires **ongoing monitoring**: the stability and performance tests above, rerun on new data at a set frequency, with triggers that send the model back for review. ModelForge does not monitor a deployed model; it has no deployment. The nearest things it has are different in kind: the inventory's next-review field, and replay, which proves on the Overview tab's reproducibility badge that a sealed run reproduces byte for byte. Reproducibility shows the evidence is sound, not that the model still performs. The design memo lists monitoring as a gap.

### Documentation and audit trail

Running through all five is documentation sufficient for a third party to understand and reproduce the model. ModelForge records every artifact by hash and every step, tool call, model call and decision as an event in a hash-chained log, and seals the run with a manifest. The **Lineage** tab shows the artifacts, the event chain with a verify button, and the manifest; the **LLM calls** tab shows every model call with its prompt version and cost.

## The regulatory landscape

ModelForge uses US supervisory guidance as its frame, and the framing matters because it is easy to get wrong.

**SR 11-7**, issued by the Federal Reserve in 2011, was the model risk management standard for fifteen years. Its core ideas are conceptual soundness, effective challenge, independent validation, ongoing monitoring, and documentation sufficient for a third party to reproduce the model. Those ideas shaped this system's pipeline and its document template, which is why the vocabulary above follows them.

**SR 26-2**, issued by the Federal Reserve on 17 April 2026, with OCC Bulletin 2026-13 as its parallel, superseded SR 11-7 and SR 21-8. Its text places generative and agentic AI models outside its scope as novel and rapidly evolving, while stating that a bank's broader risk management and governance practices should still govern them. An interagency request for information on the governance of generative and agentic AI has been signalled without a date.

The framing that follows, and that every ModelForge document uses: the regulator retired SR 11-7 and left agentic systems in a governance gap, and ModelForge shows what effective challenge, lineage and human accountability look like when they are applied to an agentic system anyway. The checks it applies to its own agents are its design choices, not techniques the guidance names.

Three other frameworks appear in the knowledge corpus, the `knowledge-base/` directory, as context for the documentation agent, so that it can explain how a PD would be used. They are context, not compliance claims, and ModelForge claims compliance with nothing.

- **Basel IRB** ([`basel-irb.md`](../knowledge-base/basel-irb.md)): the internal-ratings-based approach to credit risk capital, which defines PD, LGD and EAD.
- **IFRS 9** ([`ifrs9-ecl.md`](../knowledge-base/ifrs9-ecl.md)): the international accounting standard for expected credit losses, with twelve-month and lifetime loss stages.
- **CECL** ([`cecl-asc-326.md`](../knowledge-base/cecl-asc-326.md)): the US current expected credit loss standard, ASC 326, which requires lifetime expected losses from origination.

The corpus also holds summaries of SR 11-7, SR 26-2 and the OCC bulletin, and the project's own modeling standards ([`modeling-standards.md`](../knowledge-base/modeling-standards.md)), which restate the thresholds and test definitions above. Every regulatory claim the documentation agent makes must cite one of those passages, or its section is flagged; the [knowledge layer reference](reference/knowledge.md) explains the citation markers.

## Glossary

Terms from the architecture spec's glossary are merged here with the ones this primer introduces.

- **AUC / Gini / KS**: discrimination measures. AUC is the probability that a defaulter is ranked above a non-defaulter; Gini = 2 AUC - 1; KS is the largest gap between the cumulative score distributions of goods and bads.
- **Basel IRB**: the internal-ratings-based approach of the Basel framework, under which a bank with permission estimates its own risk parameters for capital. Context here, not a compliance claim.
- **Benchmark**: a comparison model, here the LightGBM challenger, that shows what the champion gives up.
- **Calibration**: mapping a model's output so that its PDs match observed default rates; Platt scaling in the `build` node.
- **CECL**: current expected credit loss, the US accounting standard ASC 326. Context only.
- **Champion / challenger model**: the model proposed for use, and the alternative fitted beside it as a benchmark.
- **Conceptual soundness**: whether a model's design, data and assumptions are fit for its intended use.
- **CSI**: characteristic stability index, the PSI of one input feature.
- **Default**: the credit event the model predicts; bankruptcy is its proxy in this project.
- **Effective challenge**: SR 11-7's requirement that validation be performed by people with incentives, competence and influence independent of model development.
- **E/O ratio**: expected defaults over observed defaults; one means the PD level is right on aggregate.
- **Four-eyes rule**: a decision must be taken by someone other than the person who initiated the work.
- **Grade**: a band of obligors with similar PD, the unit of calibration testing and of rating reports.
- **Hosmer-Lemeshow test**: a goodness-of-fit test of predicted against observed defaults across grades.
- **IFRS 9**: the international accounting standard for financial instruments, including expected credit loss. Context only.
- **Leakage**: a feature that encodes the outcome or information not available at decision time.
- **Model inventory**: the register of every model in use, its status, owner and review schedule.
- **MRM**: model risk management, the second-line function that validates and approves models.
- **Monotonicity**: a feature's default rate moving in one direction across its bins.
- **Obligor**: the borrower a PD is estimated for.
- **Out-of-time (OOT)**: a holdout from a later period than training; the honest test of a credit model.
- **PD / LGD / EAD**: probability of default, loss given default, exposure at default. The three Basel IRB risk parameters. This project builds PD only.
- **PDO**: points to double the odds, the scaling that turns log-odds into scorecard points.
- **Platt scaling**: calibration by a logistic fit on the raw score.
- **PSI / CSI**: population and characteristic stability indices; distribution shift of scores and features.
- **Scorecard**: a logistic model presented as points per feature bin, summed into a score.
- **Sign-off**: the final, accountable approval of the model and its documentation, taken at `gate_signoff`.
- **SR 11-7 / SR 26-2**: the Federal Reserve's model risk guidance of 2011, and the guidance of 17 April 2026 that superseded it and SR 21-8 and places generative and agentic AI outside its scope.
- **Vintage**: a cohort of obligors grouped by the period their observation starts; here, a forecast-horizon file.
- **WoE / IV**: weight of evidence and information value. WoE transforms a binned feature into log-odds; IV summarises a feature's predictive power.
- **Wrong sign**: a coefficient whose direction contradicts the business expectation; on WoE inputs, a non-negative one.
