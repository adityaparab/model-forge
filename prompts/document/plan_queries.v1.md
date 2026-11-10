---
purpose: document
version: 1
output_schema: QueryPlan
description: User prompt for the documentation agent that plans the knowledge retrieval queries for one section.
---
The section to be written, with its guidance, the aliases of the artifacts it may cite, the
artifact kinds the run has not produced, and the run's regulatory scope:

```json section
{{ section_json }}
```

Task: plan the knowledge-base retrieval for this section as a `QueryPlan`. Return between zero
and three short search queries, and only where the section's guidance calls for regulatory or
methodological support (supervisory guidance, accounting standards, modelling practice). A
section whose content comes entirely from the run's artifacts needs no queries; return an empty
list for it.
