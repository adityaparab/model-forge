---
purpose: challenge
version: 1
output_schema: ReviewDraft
description: User prompt for the challenger with the rendered document, the validation report, the plan, the passages and the pre-check findings.
---
The rendered model development document under review. Its sections open with headings of the
form `## <n>. <Title>`:

```markdown document
{{ document_md }}
```

The validation report excerpt, with its `tests` (each with `test_id`, `name`, `status`,
`value`, `threshold` and `message`) and its `overall` status. Cite it as
`[A:validation#<path>]`:

```json validation
{{ validation_json }}
```

The approved modeling plan. Cite it as `[A:plan#<path>]`:

```json plan
{{ plan_json }}
```

The knowledge passages retrieved for this review, each with the citation id to cite it by as
`[K:<citation_id>]`:

```json passages
{{ passages_json }}
```

The findings the deterministic pre-checks have already raised; do not repeat them:

```json prechecks
{{ prechecks_json }}
```

Task: list, as a `ReviewDraft`, the material findings an independent reviewer would raise
against this document, each grounded in evidence by the evidence grammar, with a severity by
the severity meanings, a category, a specific title and description, and one required action.
Return an empty `findings` list when you find nothing material.
