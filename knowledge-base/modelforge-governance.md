---
id: modelforge-governance
title: "ModelForge Governance Controls: gates, four-eyes rule, lineage, replay and redaction"
source: "ModelForge project; architecture design, sections 2 (regulatory framing), 7.5 (redaction) and 10 (lineage and governance)"
authority: "ModelForge project (internal standard)"
date: "2026-11-10"
url: "knowledge-base/modelforge-governance.md"
kind: internal
note: "Describes this system's own controls. The url is the repository path because this document has no external home."
---

# ModelForge Governance Controls

This document describes the controls the system applies to its own runs: who must approve what, how the record of a run is protected, and what may leave the system in a prompt to a language model.

## Regulatory framing

SR 26-2, issued on April 17, 2026 by the Federal Reserve Board, the OCC and the FDIC, superseded SR 11-7 and SR 21-8. Its text places generative AI and agentic AI models outside its scope, describing them as novel and rapidly evolving, and states that a banking organisation's broader risk management and governance practices should still guide the controls for tools and systems the guidance does not cover. Its principles apply to traditional statistical and quantitative models.

Two consequences follow for this system. The PD scorecards it produces are traditional statistical models, so the themes of the guidance (sound development, validation, monitoring, governance and documentation) bear on them. The agentic workflow that helps produce them falls outside that guidance, so the controls below are the system's own application of broader governance practice to an agentic process. They are a design choice, not a claim that the system meets any regulatory standard.

## Human gates

A run passes three human gates. No gate can be skipped, and every decision is recorded in the approvals table with the approver, the decision, any comment and, where relevant, the change to the plan.

- **Scope gate.** After the scope agent turns the modeling brief into a typed modeling plan, a person approves the plan, edits it, or rejects it. Profiling, splitting and model building start only after this gate.
- **Model gate.** After the champion and challenger are built and the validation report is produced, a person accepts the model, sends it back to feature work or model building for a bounded number of adjustments, or rejects it.
- **Sign-off gate.** After the development document is drafted, independently challenged and remediated, a person approves it, approves it with conditions, or rejects it. The decision sets the model's status in the model inventory.

## Four-eyes rule

The model gate and the sign-off gate reject an approver who is the same person as the run's requester. A model cannot be accepted, and a document cannot be signed off, by the person who asked for it. Identity comes from a request header mapped to roles in configuration, and the identity mechanism is designed to be replaceable.

## Hash-chained events

Every significant action in a run is written as an event to an append-only table. Each event stores the hash of the previous event, and its own hash is the SHA-256 of the previous hash joined to the canonical form of its payload. Altering or removing any earlier event breaks every hash after it. A verify command walks the chain for a run and also checks the hash of every artifact the run references.

## Content-addressed artifacts

Every artifact a run produces, from data profiles to the scorecard and the rendered document, is stored under the SHA-256 of its bytes. An artifact's identifier is therefore a commitment to its content: the same bytes always have the same identifier, and any change to the bytes changes the identifier. Models are stored in inspectable formats (the champion as a JSON scorecard, the challenger as LightGBM native text) and serialisation formats that execute code on load are not used.

## Replay

A sealed run carries a manifest with every input hash, artifact hash, version, seed and prompt hash. Replay re-executes the deterministic steps from that manifest and asserts that each artifact hash matches the original. Steps that call a language model replay from the stored responses by default; an option re-runs them live and reports the differences.

## Redaction of outbound prompts

Prompts sent to a language model contain tool summaries and artifact excerpts, never dataset rows. Column names are replaced with neutral identifiers at ingestion, with a dictionary artifact that maps them back. Before any prompt leaves the system, the gateway scans the outbound text for field names marked restricted in the run configuration and for identifier-like patterns. If it finds one, it refuses to send the prompt with a typed error rather than silently masking the text, so the failure is visible and recorded.

## Accountability

Every language model call is recorded with its purpose, provider, model, prompt name, version and hash, token counts, cost and whether the output matched its schema. The final decisions on a model always rest with named people at the gates.
