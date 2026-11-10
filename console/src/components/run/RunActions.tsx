import { useState } from "react";

import { useAbort, useResume } from "../../api/queries";

export const primaryButton =
  "inline-flex h-7 items-center rounded-sm bg-accent px-3 text-sm font-medium text-bg hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50";
export const secondaryButton =
  "inline-flex h-7 items-center rounded-sm border border-border bg-panel-2 px-3 text-sm text-text hover:border-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50";

function ActionError({ error }: { error: Error | null }) {
  if (error === null) return null;
  return (
    <span role="alert" className="text-xs text-status-attention">
      {error.message}
    </span>
  );
}

/** Resume with an optional new cost cap; the server keeps the current cap when it is empty. */
export function ResumeAction({ runId }: { runId: string }) {
  const resume = useResume(runId);
  const [cap, setCap] = useState("");
  return (
    <span className="inline-flex items-center gap-2">
      <input
        aria-label="New cost cap (USD)"
        type="number"
        min={0}
        step={0.01}
        value={cap}
        onChange={(event) => setCap(event.target.value)}
        placeholder="cap (USD)"
        className="h-7 w-28 rounded-sm border border-border bg-panel-2 px-2 font-mono text-sm text-text tabular-nums placeholder:text-muted/60 focus:border-accent focus:outline-none"
      />
      <button
        type="button"
        disabled={resume.isPending}
        onClick={() => resume.mutate(cap === "" ? {} : { cost_cap_usd: Number(cap) })}
        className={primaryButton}
      >
        Resume
      </button>
      <ActionError error={resume.error} />
    </span>
  );
}

/** Abort the run for good; the server refuses while it is running (pause first). */
export function AbortAction({ runId }: { runId: string }) {
  const abort = useAbort(runId);
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        disabled={abort.isPending}
        onClick={() => {
          if (window.confirm(`Abort ${runId}? An aborted run cannot be resumed.`)) abort.mutate();
        }}
        className={`${secondaryButton} hover:border-status-attention hover:text-status-attention`}
      >
        Abort
      </button>
      <ActionError error={abort.error} />
    </span>
  );
}

export { ActionError };
