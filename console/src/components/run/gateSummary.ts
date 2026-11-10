import type { Schema } from "../../api/client";
import type { Severity } from "../SeverityPill";

/**
 * Typed readers over the untyped gate summaries (`GateView.summary` is `{[key]: unknown}` in
 * the schema). Each reader tolerates a missing or malformed field, so an older run's summary
 * still renders.
 */

export type ModelingPlan = Schema<"ModelingPlan">;
export type Counts = Record<string, number>;

export type ScopeSummary = {
  plan: ModelingPlan | null;
  rationale: string | null;
  assumptions: string[];
};

export type ModelSummary = {
  overall: string | null;
  statuses: Record<string, string>;
  selected: string[];
  buildLoops: number;
};

export type SignoffSummary = {
  findings: {
    n: number;
    bySeverity: Counts;
    byStatus: Counts;
    maxOpenSeverity: string | null;
    rounds: number;
  };
  responses: { n: number; accepted: number; disputed: number; regenerated: number } | null;
  completeness: { complete: boolean; missingSections: string[] } | null;
  validationOverall: string | null;
};

export type BlockingFinding = { id: string; severity: string; title: string };

const SEVERITIES: readonly string[] = ["critical", "high", "medium", "low"] satisfies Severity[];

export function isSeverity(value: unknown): value is Severity {
  return typeof value === "string" && SEVERITIES.includes(value);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
}

function counts(value: unknown): Counts {
  return Object.fromEntries(
    Object.entries(record(value)).filter((entry): entry is [string, number] =>
      Number.isFinite(entry[1]),
    ),
  );
}

/** A parsed plan: any JSON object passes here; the server validates the `ModelingPlan`. */
export function asPlan(value: unknown): ModelingPlan | null {
  return isRecord(value) ? (value as ModelingPlan) : null;
}

export function scopeSummary(summary: Record<string, unknown>): ScopeSummary {
  return {
    plan: asPlan(summary.plan),
    rationale: text(summary.rationale),
    assumptions: strings(summary.assumptions),
  };
}

export function modelSummary(summary: Record<string, unknown>): ModelSummary {
  const validation = record(summary.validation);
  return {
    overall: text(validation.overall),
    statuses: Object.fromEntries(
      Object.entries(record(validation.statuses)).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string",
      ),
    ),
    selected: strings(summary.selected),
    buildLoops: count(summary.build_loops),
  };
}

export function signoffSummary(summary: Record<string, unknown>): SignoffSummary {
  const findings = record(summary.findings);
  const responses = isRecord(summary.responses) ? summary.responses : null;
  const completeness = isRecord(summary.completeness) ? summary.completeness : null;
  return {
    findings: {
      n: count(findings.n_findings),
      bySeverity: counts(findings.by_severity),
      byStatus: counts(findings.by_status),
      maxOpenSeverity: text(findings.max_open_severity),
      rounds: count(findings.rounds),
    },
    responses:
      responses === null
        ? null
        : {
            n: count(responses.n_responses),
            accepted: count(responses.n_accepted),
            disputed: count(responses.n_disputed),
            regenerated: count(responses.n_sections_regenerated),
          },
    completeness:
      completeness === null
        ? null
        : {
            complete: completeness.complete === true,
            missingSections: strings(completeness.missing_sections),
          },
    validationOverall: text(summary.validation_overall),
  };
}

/** The findings a blocking refusal (`409` kind `approval`) lists in `extra.findings`. */
export function blockingFindings(extra: Record<string, unknown>): BlockingFinding[] {
  if (!Array.isArray(extra.findings)) return [];
  return extra.findings.filter(isRecord).map((item) => ({
    id: text(item.id) ?? "?",
    severity: text(item.severity) ?? "",
    title: text(item.title) ?? "",
  }));
}

/** The top-level plan fields whose value differs from the proposal. */
export function changedFields(
  proposal: ModelingPlan | null,
  edited: ModelingPlan | null,
): string[] {
  if (proposal === null || edited === null) return [];
  const keys = new Set([...Object.keys(proposal), ...Object.keys(edited)]);
  const before: Record<string, unknown> = proposal;
  const after: Record<string, unknown> = edited;
  return [...keys].filter((key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
}
