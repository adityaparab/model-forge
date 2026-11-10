import type { Schema } from "../../api/client";

type GroundingReport = Schema<"GroundingReport">;
type CheckKey = Exclude<keyof GroundingReport, "ok">;

/** The four checks, in the order the renderer reports them. */
const CHECKS = [
  ["ungrounded_numbers", "Ungrounded numbers"],
  ["unresolved_markers", "Unresolved markers"],
  ["uncited_regulatory_sentences", "Uncited regulatory sentences"],
  ["foreign_passages", "Foreign passages"],
] as const satisfies ReadonlyArray<readonly [CheckKey, string]>;

/** Broken references are red; text the author has yet to cite is amber. */
const BROKEN: readonly CheckKey[] = ["unresolved_markers", "foreign_passages"];

const pill = "inline-flex h-5 items-center rounded-sm border px-2 text-xs font-medium whitespace-nowrap";

/** A section's grounding verdict: "grounded", or each failure list with its count and entries. */
export function GroundingBadge({ grounding }: { grounding: GroundingReport | null }) {
  if (grounding === null) {
    return (
      <span role="status" aria-label="Grounding" className={`${pill} border-border text-muted`}>
        not checked
      </span>
    );
  }
  if (grounding.ok) {
    return (
      <span
        role="status"
        aria-label="Grounding"
        className={`${pill} border-status-completed text-status-completed`}
      >
        grounded
      </span>
    );
  }
  const broken = BROKEN.some((key) => grounding[key].length > 0);
  const tone = broken
    ? "border-status-attention text-status-attention"
    : "border-status-awaiting text-status-awaiting";
  return (
    <div role="status" aria-label="Grounding" className="text-xs">
      <details>
      <summary
        className={`inline-flex min-h-5 cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-0.5 rounded-sm border px-2 py-0.5 font-medium ${tone} focus-visible:outline-2 focus-visible:outline-accent`}
      >
        <span className="font-semibold">{broken ? "ungrounded" : "needs citations"}</span>
        {CHECKS.map(([key, label]) => (
          <span
            key={key}
            className={`whitespace-nowrap ${grounding[key].length === 0 ? "opacity-50" : ""}`}
          >
            {label} {grounding[key].length}
          </span>
        ))}
      </summary>
      <ul className="mt-2 flex flex-col gap-1.5 rounded-sm border border-border bg-panel p-2">
        {CHECKS.filter(([key]) => grounding[key].length > 0).map(([key, label]) => (
          <li key={key} className="flex flex-col gap-0.5">
            <span className="text-muted">{label}</span>
            <ul className="flex flex-wrap gap-1">
              {grounding[key].map((entry, index) => (
                <li
                  key={`${index}:${entry}`}
                  className="rounded-sm bg-panel-2 px-1.5 py-0.5 font-mono text-[0.6875rem] break-all text-text"
                >
                  {entry}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      </details>
    </div>
  );
}
