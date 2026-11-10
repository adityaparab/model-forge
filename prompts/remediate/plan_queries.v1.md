---
purpose: remediate
version: 1
output_schema: QueryPlan
description: User prompt for the remediation agent that plans the knowledge retrieval queries for rewriting one section against the reviewer's findings.
---
The section to be rewritten, with its guidance, the aliases of the artifacts it may cite, the
artifact kinds the run has not produced, and the run's regulatory scope:

```json section
{{ section_json }}
```

The reviewer's findings this section must address, each with its title, description, evidence
and required action:

```json findings
{{ findings_json }}
```

Task: plan the knowledge-base retrieval for this rewrite as a `QueryPlan`. Return between zero
and three short search queries, and only where addressing a finding calls for regulatory or
methodological support (supervisory guidance, accounting standards, modelling practice). A
finding resolved entirely from the run's artifacts needs no queries; return an empty list when
none of the findings does.
