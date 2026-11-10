import { useState } from "react";

import { useFindings } from "../../api/queries";
import type { Schema } from "../../api/client";
import { EvidenceList, FindingCard, FindingSeverity } from "../../components/run/FindingCard";
import { Mono } from "../../components/Mono";
import { useRunDetail } from "./useRunDetail";

type FindingRow = Schema<"FindingRow">;
type FindingResponse = Schema<"FindingResponse">;
type FindingsSummary = Schema<"FindingsSummary">;

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];

function bySeverity(a: string, b: string): number {
  const rank = (s: string) => (SEVERITY_ORDER.includes(s) ? SEVERITY_ORDER.indexOf(s) : SEVERITY_ORDER.length);
  return rank(a) - rank(b);
}

function Counts({ label, counts }: { label: string; counts: Record<string, number> }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-muted">{label}</span>
      {Object.entries(counts).map(([key, n]) => (
        <span key={key} className="rounded-sm border border-border px-1.5 font-mono text-xs">
          {key} {n}
        </span>
      ))}
    </div>
  );
}

function SummaryBar({ summary }: { summary: FindingsSummary }) {
  const severities = Object.fromEntries(
    Object.entries(summary.by_severity).sort(([a], [b]) => bySeverity(a, b)),
  );
  return (
    <section
      aria-label="Findings summary"
      className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-sm border border-border bg-panel px-3 py-2"
    >
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-base font-semibold">{String(summary.n_findings)}</span>
        <span className="text-xs text-muted">findings</span>
      </div>
      <Counts label="Status" counts={summary.by_status} />
      <Counts label="Severity" counts={severities} />
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted">Max open</span>
        <span aria-label="Max open severity">
          {summary.max_open_severity === null ? (
            <span className="text-xs text-muted">none</span>
          ) : (
            <FindingSeverity severity={summary.max_open_severity} />
          )}
        </span>
      </div>
      <div className="flex items-center gap-3 text-xs text-muted">
        <span>
          challenge rounds <Mono>{String(summary.challenge_rounds)}</Mono>
        </span>
        <span>
          remediation rounds <Mono>{String(summary.remediation_rounds)}</Mono>
        </span>
      </div>
    </section>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-7 rounded-sm border border-border bg-panel px-2 text-[0.8125rem] text-text focus-visible:outline-2 focus-visible:outline-accent"
      >
        <option value="">all</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Section ids regenerated in a round, in the order the responses name them. */
function regeneratedIn(responses: FindingResponse[]): string[] {
  return [...new Set(responses.flatMap((response) => response.sections_regenerated ?? []))];
}

function ResponsesList({ round, responses }: { round: number; responses: FindingResponse[] }) {
  if (responses.length === 0) return null;
  return (
    <ul aria-label={`Responses in round ${round}`} className="flex flex-col divide-y divide-border rounded-sm border border-border">
      {responses.map((response) => (
        <li key={response.finding_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-1.5 text-[0.8125rem]">
          <Mono>{response.finding_id}</Mono>
          <span
            className={`font-mono text-xs ${response.decision === "dispute" ? "text-status-awaiting" : "text-status-completed"}`}
          >
            {response.decision}
          </span>
          <span className="min-w-0 flex-1">{response.action}</span>
          {(response.evidence ?? []).length > 0 && <EvidenceList items={response.evidence ?? []} />}
        </li>
      ))}
    </ul>
  );
}

function RoundGroup({
  round,
  findings,
  responses,
}: {
  round: number;
  findings: FindingRow[];
  responses: FindingResponse[];
}) {
  const regenerated = regeneratedIn(responses);
  const shownResponses = responses.filter((response) =>
    findings.some((finding) => finding.id === response.finding_id),
  );
  return (
    <section aria-label={`Round ${round}`} className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">Round {round}</h3>
      <div className="grid gap-2 xl:grid-cols-2">
        {findings.map((finding) => (
          <FindingCard key={finding.row_id} finding={finding} />
        ))}
      </div>
      <ResponsesList round={round} responses={shownResponses} />
      {regenerated.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          Sections regenerated
          <ul aria-label={`Sections regenerated in round ${round}`} className="flex flex-wrap gap-1.5">
            {regenerated.map((section) => (
              <li key={section} className="rounded-sm bg-panel-2 px-1.5 font-mono text-[0.6875rem]">
                {section}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/** Findings for sign-off: the summary, filters, then each raising round with its responses. */
export function FindingsTab() {
  const { run } = useRunDetail();
  const findings = useFindings(run.id);
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");

  if (findings.isError) {
    return (
      <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
        Could not load the findings: {findings.error.message}
      </p>
    );
  }
  if (findings.data === undefined) return <p className="text-muted">Loading the findings...</p>;

  const { summary, responses } = findings.data;
  const shown = findings.data.findings.filter(
    (finding) => (status === "" || finding.status === status) && (severity === "" || finding.severity === severity),
  );
  const rounds = [...new Set(shown.map((finding) => finding.round))].sort((a, b) => a - b);

  return (
    <div className="flex flex-col gap-4">
      <SummaryBar summary={summary} />
      <div className="flex flex-wrap items-center gap-4">
        <FilterSelect label="Status" value={status} options={Object.keys(summary.by_status)} onChange={setStatus} />
        <FilterSelect
          label="Severity"
          value={severity}
          options={Object.keys(summary.by_severity).sort(bySeverity)}
          onChange={setSeverity}
        />
      </div>
      {summary.n_findings === 0 && <p className="text-muted">No findings were raised for this run.</p>}
      {summary.n_findings > 0 && rounds.length === 0 && (
        <p className="text-muted">No findings match the filters.</p>
      )}
      {rounds.map((round) => (
        <RoundGroup
          key={round}
          round={round}
          findings={shown.filter((finding) => finding.round === round)}
          responses={responses.filter((response) => response.round === round)}
        />
      ))}
    </div>
  );
}
