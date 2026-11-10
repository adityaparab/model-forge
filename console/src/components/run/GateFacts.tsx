import type { ReactNode } from "react";

/** A dense grid of label and value pairs for a gate summary. */
export function Facts({ children }: { children: ReactNode }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">{children}</dl>
  );
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-xs leading-6 text-muted">{label}</dt>
      <dd className="min-w-0 leading-6">{children}</dd>
    </>
  );
}

const OUTCOME: Record<string, string> = {
  pass: "text-status-completed",
  warn: "text-status-awaiting",
  fail: "text-status-attention",
};

/** A validation outcome (`pass`, `warn`, `fail`) in its colour. */
export function Outcome({ value }: { value: string | null }) {
  if (value === null) return <span className="text-muted">-</span>;
  return <span className={`font-mono font-medium ${OUTCOME[value] ?? "text-text"}`}>{value}</span>;
}

/** `{a: 1, b: 2}` as `a 1 · b 2`, or a dash when empty. */
export function CountList({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  if (entries.length === 0) return <span className="text-muted">-</span>;
  return (
    <span className="inline-flex flex-wrap gap-x-3 font-mono text-[0.8125rem] tabular-nums">
      {entries.map(([key, n]) => (
        <span key={key}>
          <span className="text-muted">{key}</span> {n}
        </span>
      ))}
    </span>
  );
}
