This page is the reference for the knowledge layer; the README is the front door.

# Knowledge layer

The agents cite a small corpus in `knowledge-base/` (`MODELFORGE_KNOWLEDGE_DIR`), served by the `knowledge` MCP server (`packages/knowledge`, `modelforge_knowledge`):

| id | Kind | What it is | Source |
| --- | --- | --- | --- |
| `sr11-7` | primary | SR 11-7, Supervisory Guidance on Model Risk Management (2011), full text; superseded, kept as the structure the development document follows | Federal Reserve Board and OCC, public domain |
| `sr26-2` | primary | SR 26-2, Revised Guidance on Model Risk Management (2026), letter and attachment, full text | Federal Reserve Board, FDIC and OCC, public domain |
| `occ-2026-13` | summary | The OCC bulletin transmitting the same revised guidance | Office of the Comptroller of the Currency; summary in our own words |
| `cecl-asc-326` | summary | CECL expected credit loss concepts relevant to PD | FASB ASU 2016-13 (Topic 326); summary in our own words |
| `ifrs9-ecl` | summary | IFRS 9 staging and lifetime expected credit loss | IASB, IFRS 9; summary in our own words |
| `basel-irb` | summary | PD, LGD and EAD under the internal ratings-based approach | Basel Committee, CRE30 to CRE36; summary in our own words |
| `modeling-standards` | internal | Required document sections, default thresholds, mandatory validation tests, the two dataset proxies | This project |
| `modelforge-governance` | internal | How this system governs itself: gates, four-eyes rule, lineage, replay, redaction | This project |

The corpus is chunked by heading and indexed with BM25 (no embeddings). Each chunk has a stable citation id, `k_` followed by twelve hex characters of a hash of its document, section and text. The `knowledge` server has three tools: `search_regulation` (ranked passages for a query, optionally restricted to some documents), `get_passage` (one passage by citation id) and `get_index` (the stored index). The index is a content-addressed `knowledge_index` artifact pinned in every run manifest, so a replay compares it like any deterministic artifact and detects a changed corpus.

**Citations.** Generated text cites with two markers: `[A:<artifact_id>#<path>]` for a value in a run artifact and `[K:<citation_id>]` for a knowledge passage (the agent writes an alias such as `[A:discrimination#by_split.oot.auc]`, which the node expands to the artifact id). A path is a sequence of dotted keys. A step applied to a list selects the element whose discriminator equals the step, trying `split`, `test_id`, `feature`, `grade` and `name`; a discriminator that itself contains dots is written in brackets, as in `tests[disc.auc.oot].status`; and `.count` gives a list's length. The same grammar resolves on the excerpt the agent saw and on the stored artifact.

**Grounding checks.** Every drafted section is checked before it is stored. Every number in a sentence must match a value cited by an `[A:]` marker in that sentence, at the number's own precision (a value written to three significant figures is compared after rounding to three); every marker must resolve; and a sentence that names a regulation must carry a `[K:]` marker naming a passage retrieved for that section. A section that fails is regenerated once with the failures fed back to the model; if it still fails, it is stored with a visible `REQUIRES AUTHOR ATTENTION` banner listing the failures, and the `document_sections` summary counts it in `n_flagged_sections`. Nothing is silently dropped.

**Regulatory framing.** SR 26-2 superseded SR 11-7 and SR 21-8 on 17 April 2026, and it places generative and agentic AI outside its scope, while broader risk management expectations still apply to such tools. This project keeps SR 11-7's document structure for the development document, because that is the structure model validators know, and governs its own agents anyway: gates, four-eyes approval, lineage, cost caps, redaction and grounding apply to every model call even though the guidance does not require them.

[Back to the README](../../README.md)
