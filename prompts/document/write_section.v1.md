---
purpose: document
version: 1
output_schema: SectionDraft
description: User prompt for the documentation agent with the section, its artifact excerpts, the retrieved passages and any failures to fix.
---
The section to write, with its guidance, the aliases you may cite (each with what its excerpt
holds), the artifact kinds the run has not produced, and the run's regulatory scope. When the
section lists `run_artifacts`, those ids describe the run's lineage and are not citable: never
put one in a marker, and write no numbers taken from them.

```json section
{{ section_json }}
```

The excerpts, keyed by alias. Cite a value as `[A:<alias>#<path>]`, where `<path>` is the dotted
path to the value: a key selects that field of an object, and in a list of objects a step selects
the element whose discriminator equals it (`split` in `by_split`, `test_id` in `tests`, `feature`
in feature lists, `grade` in `grades`). So `by_split.oot.auc` is the `auc` of the `by_split`
element whose `split` is `oot`.

```json excerpts
{{ excerpts_json }}
```

The knowledge passages retrieved for this section, each with the citation id to cite it by as
`[K:<citation_id>]`:

```json passages
{{ passages_json }}
```
{% if failures_json %}

Your previous draft of this section failed the grounding checks. Fix each listed failure: give
every ungrounded number a marker whose value it states or remove it, correct or remove each
unresolved marker, cite a supplied passage in each uncited regulatory sentence, and replace each
foreign passage with one from the passages block.

```json failures
{{ failures_json }}
```
{% endif %}

Task: write the section body as a `SectionDraft`, following the writing rules.
