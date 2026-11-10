---
purpose: remediate
version: 1
output_schema: FindingResponseDraft
description: User prompt for the remediation agent with one reviewer finding, the current section bodies and the artifact excerpts to answer it from.
---
The reviewer's finding to answer, with its `id`, `round`, `severity`, `category`, `title`,
`description`, the `evidence` it rests on (template section ids and artifact or passage
markers), the `required_action` that would resolve it, its `status`, its `source` (`precheck`
for a deterministic pre-check, `review` for the reviewer) and the pre-check's `check_key`:

```json finding
{{ finding_json }}
```

The current bodies of the document's sections, keyed by template section id:

```json sections
{{ sections_json }}
```

The artifact excerpts, keyed by alias. Cite a value as `[A:<alias>#<path>]`, where `<path>`
leads from the top of that alias's excerpt to the value. The path rules:

1. A key selects that field of an object, and steps are joined with dots:
   `[A:plan#target.horizon]` is the `horizon` field of the plan's `target`.
2. In a list of objects, a step selects the element whose discriminator equals it. The
   discriminators are `split` for `by_split`, `test_id` for `tests`, `feature` for feature lists,
   `grade` for `grades` and `name` for `columns`. So `[A:discrimination#by_split.oot.auc]` is the
   `auc` of the `by_split` element whose `split` is `oot`.
3. A discriminator value that itself contains a dot is written in brackets:
   `[A:validation#tests[disc.auc.oot].status]` is the `status` of the test whose `test_id` is
   `disc.auc.oot`. Never write such a value as plain dotted steps.
4. A list the excerpt shows as `{"count": N}` is cited by its length with a final `.count`
   step: `[A:selection#selected.count]` is the number of selected features.

```json excerpts
{{ excerpts_json }}
```

Task: answer the finding as a `FindingResponseDraft`. Set `decision` to `accept` with a
concrete `action` and, when the fix is documentation, the section ids to rewrite in
`sections_to_regenerate`; or set it to `dispute` with the reason as the `action` and the
artifact markers that show the finding is wrong in `evidence`. Never dispute without evidence.
