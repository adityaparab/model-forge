import type { ReactNode } from "react";
import { Link } from "react-router";

import type { Schema } from "../../api/client";
import { Citation } from "../doc/Citation";
import { splitMarkers } from "../doc/markers";
import { Mono } from "../Mono";
import { SeverityPill, type Severity } from "../SeverityPill";

type FindingRow = Schema<"FindingRow">;
type ResolvedCitation = Schema<"ResolvedCitation">;

const SEVERITIES: readonly string[] = ["critical", "high", "medium", "low"] satisfies Severity[];

function isSeverity(value: string): value is Severity {
  return SEVERITIES.includes(value);
}

/** A severity the pill knows, or the raw value outlined in the neutral tone. */
export function FindingSeverity({ severity }: { severity: string }) {
  if (isSeverity(severity)) return <SeverityPill severity={severity} />;
  return (
    <span className="inline-flex h-5 items-center rounded-sm border border-border px-2 text-xs text-muted">
      {severity}
    </span>
  );
}

/** Filled, like run statuses: open and disputed findings still need the reviewer's eye. */
const STATUS_TONE: Record<string, string> = {
  open: "bg-status-attention text-text",
  accepted: "bg-status-running text-text",
  disputed: "bg-status-awaiting text-bg",
  closed: "bg-status-completed text-bg",
};

function FindingStatusPill({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex h-5 items-center rounded-sm px-2 text-xs font-medium whitespace-nowrap ${
        STATUS_TONE[status] ?? "bg-status-queued text-text"
      }`}
    >
      {status}
    </span>
  );
}

function Chip({ children }: { children: string }) {
  return (
    <span className="inline-flex h-5 items-center rounded-sm bg-panel-2 px-1.5 font-mono text-[0.6875rem] text-muted">
      {children}
    </span>
  );
}

/**
 * One evidence item: a citation marker becomes a chip with the API's resolution of it (the
 * unresolved tone only when the API could not resolve it), anything else is a section id and
 * links to that section of the document.
 */
function EvidenceItem({ item, citations }: { item: string; citations: readonly ResolvedCitation[] }) {
  const parts = splitMarkers(item);
  if (parts.length === 1 && parts[0].kind === "marker") {
    const marker = parts[0].marker;
    return <Citation marker={marker} citation={citations.find((c) => c.marker === marker)} />;
  }
  return (
    <Link
      to={`../document#${encodeURIComponent(item)}`}
      className="font-mono text-[0.75rem] text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
    >
      {item}
    </Link>
  );
}

/** The evidence of a finding or a response, inline, its markers resolved through `citations`. */
export function EvidenceList({ items, citations }: { items: string[]; citations: readonly ResolvedCitation[] }) {
  if (items.length === 0) return <span className="text-xs text-muted">none</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      {items.map((item) => (
        <EvidenceItem key={item} item={item} citations={citations} />
      ))}
    </span>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-x-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-[0.8125rem]">{children}</dd>
    </div>
  );
}

/** One finding as the sign-off reviewer reads it: what was found, what was asked, what happened. */
export function FindingCard({ finding }: { finding: FindingRow }) {
  return (
    <article aria-label={finding.id} className="flex flex-col gap-2 rounded-sm border border-border bg-panel p-3">
      <header className="flex flex-wrap items-center gap-2">
        <Mono>{finding.id}</Mono>
        <FindingSeverity severity={finding.severity} />
        <Chip>{finding.source}</Chip>
        <Chip>{finding.category}</Chip>
        <span className="ml-auto">
          <FindingStatusPill status={finding.status} />
        </span>
      </header>
      <h4 className="text-sm font-semibold">{finding.title}</h4>
      <p className="text-[0.8125rem] leading-relaxed text-muted">{finding.description}</p>
      <dl className="flex flex-col gap-1.5 border-t border-border pt-2">
        <Field label="Evidence">
          <EvidenceList items={finding.evidence} citations={finding.citations} />
        </Field>
        <Field label="Required action">{finding.required_action}</Field>
        <Field label="Response">
          {finding.response ?? <span className="text-xs text-muted">no response</span>}
        </Field>
        {finding.response_evidence !== null && finding.response_evidence.length > 0 && (
          <Field label="Response evidence">
            <EvidenceList items={finding.response_evidence} citations={finding.citations} />
          </Field>
        )}
        <Field label="Verdict">
          <span className="flex flex-wrap items-center gap-3">
            <span>{finding.verdict ?? <span className="text-xs text-muted">no verdict</span>}</span>
            {finding.closed_round !== null && (
              <span className="font-mono text-xs text-muted">closed in round {finding.closed_round}</span>
            )}
          </span>
        </Field>
      </dl>
    </article>
  );
}
