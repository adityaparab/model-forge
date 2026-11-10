---
purpose: remediate
version: 1
output_schema: SectionDraft
description: User prompt for the remediation agent with the section, its artifact excerpts, the retrieved passages, the reviewer's findings to address and any failures to fix.
---
The section to write, with its guidance, the aliases you may cite (each with what its excerpt
holds), the artifact kinds the run has not produced, and the run's regulatory scope. When the
section lists `run_artifacts`, those ids describe the run's lineage and are not citable: never
put one in a marker, and write no numbers taken from them.

```json section
{{ section_json }}
```

The excerpts, keyed by alias. Cite a value as `[A:<alias>#<path>]`, where `<path>` leads from
the top of that alias's excerpt to the value. The path rules:

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

The knowledge passages retrieved for this section, each with the citation id to cite it by as
`[K:<citation_id>]`:

```json passages
{{ passages_json }}
```

The reviewer's findings this section must address; address each in the text. Refer to a
finding only by its title inside inline code, or not at all, and never by its id:

```json findings
{{ findings_json }}
```
{% if failures_json %}

Your previous draft of this section failed the grounding checks. Fix each listed failure: give
every ungrounded number a marker whose value it states or remove it, rewrite each unresolved
marker by the path rules above (brackets for a discriminator value containing a dot, `.count`
for a counted list) or remove it, cite a supplied passage in each uncited regulatory sentence, and replace each
foreign passage with one from the passages block.

```json failures
{{ failures_json }}
```
{% endif %}

Task: write the section body as a `SectionDraft`, following the writing rules.
