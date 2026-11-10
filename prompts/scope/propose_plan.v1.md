---
purpose: scope
version: 1
output_schema: ScopeProposal
description: User prompt for the scope agent with the brief, the standards defaults and knowledge passages.
---
The modeling brief, as submitted:

```yaml brief
{{ brief_yaml }}
```

The standards defaults: thresholds, feature policy, methodology and the sections the model
development document must contain.

```json defaults
{{ defaults_json }}
```

Knowledge passages retrieved for this plan, each with the citation id to cite it by:

```json passages
{{ passages_json }}
```

Task: propose the initial modeling plan for this brief as a `ScopeProposal` with `plan`,
`rationale`, `assumptions` and `open_questions`. Start from the defaults, apply the brief's
feature policy and any threshold overrides it gives, and keep the default methodology.

The plan must satisfy every one of these invariants; an answer that breaks one is rejected:

1. `model_name`, `portfolio`, `target`, `intended_use`, `regulatory_scope`, `tier` and
   `vintage_policy` equal the brief's values exactly.
2. `feature_policy.max_features` is at most the brief's `feature_policy.max_features`.
3. `feature_policy.exclusions` contains every exclusion the brief lists.
4. Every threshold except `eo_low` and `eo_high` lies between 0 and 1 inclusive.
5. `auc_warn <= auc_min`, `psi_warn <= psi_fail`, `gap_max <= gap_fail`, `iv_min < iv_max`
   and `eo_low < 1 < eo_high`.
6. Every `[K:<citation_id>]` in `rationale` names a citation id from the passages block.
