export type Severity = "critical" | "high" | "medium" | "low";

/** Outlined pills, so a severity never reads as a run status. */
const SEVERITY_CLASSES: Record<Severity, string> = {
  critical: "border-sev-critical text-sev-critical",
  high: "border-sev-high text-sev-high",
  medium: "border-sev-medium text-sev-medium",
  low: "border-sev-low text-sev-low",
};

export function SeverityPill({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-flex h-5 items-center rounded-sm border px-2 text-xs font-medium whitespace-nowrap ${SEVERITY_CLASSES[severity]}`}
    >
      {severity}
    </span>
  );
}
