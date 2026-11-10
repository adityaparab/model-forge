---
purpose: challenge
version: 1
output_schema: ChallengeQueryPlan
description: User prompt for the challenger that plans the knowledge retrieval queries for its review.
---
The section titles of the model development document under review:

```json sections
{{ sections_json }}
```

The regulatory scope of the modeling plan:

```json scope
{{ scope_json }}
```

The status of each validation test, keyed by test id:

```json statuses
{{ statuses_json }}
```

The findings the deterministic pre-checks have already raised:

```json prechecks
{{ prechecks_json }}
```

Task: plan the knowledge-base retrieval for your review as a `ChallengeQueryPlan`. Return
between zero and five short search queries about what supervisory guidance expects of each area
an independent reviewer must challenge: validation, limitations, monitoring, data and conceptual
soundness. Favour the areas where the statuses or the pre-check findings point to a weakness.
Return an empty list when no query would help.
