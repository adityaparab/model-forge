import { useState } from "react";
import { Link } from "react-router";

import { useRuns, type RunFilters } from "../api/queries";
import { RunsTable } from "../components/RunsTable";
import type { RunStatus } from "../components/StatusPill";
import { useGlobalEvents } from "../events/useGlobalEvents";

const STATUSES: RunStatus[] = [
  "queued",
  "running",
  "awaiting_scope",
  "awaiting_model",
  "awaiting_signoff",
  "needs_attention",
  "paused",
  "completed",
  "rejected",
  "aborted",
];

const field =
  "h-7 rounded-sm border border-border bg-panel-2 px-2 text-sm text-text focus:border-accent focus:outline-none";

function Filters({
  filters,
  onChange,
}: {
  filters: RunFilters;
  onChange: (next: RunFilters) => void;
}) {
  const set = (patch: RunFilters) => onChange({ ...filters, ...patch });
  return (
    <div className="flex flex-wrap items-center gap-4 text-xs text-muted">
      <label className="flex items-center gap-2">
        Status
        <select
          aria-label="Status"
          value={filters.status ?? ""}
          onChange={(event) => set({ status: event.target.value })}
          className={field}
        >
          <option value="">any</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {status.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2">
        Profile
        <input
          aria-label="Profile"
          value={filters.profile ?? ""}
          onChange={(event) => set({ profile: event.target.value })}
          spellCheck={false}
          className={`${field} w-36 font-mono`}
        />
      </label>
      <label className="flex items-center gap-2">
        Requester
        <input
          aria-label="Requester"
          value={filters.requester ?? ""}
          onChange={(event) => set({ requester: event.target.value })}
          spellCheck={false}
          className={`${field} w-36 font-mono`}
        />
      </label>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={filters.replays === "exclude"}
          onChange={(event) => set({ replays: event.target.checked ? "exclude" : undefined })}
          className="accent-accent"
        />
        Hide replays
      </label>
    </div>
  );
}

/** The landing page: every run, filterable, kept current by the global event stream. */
export function RunsPage() {
  const [filters, setFilters] = useState<RunFilters>({});
  const runs = useRuns(filters);
  useGlobalEvents();

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold">Runs</h1>
        <Link
          to="/runs/new"
          className="inline-flex h-7 items-center rounded-sm bg-accent px-3 text-sm font-medium text-bg hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          New run
        </Link>
      </div>
      <Filters filters={filters} onChange={setFilters} />
      {runs.isError ? (
        <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
          Could not load runs: {runs.error.message}
        </p>
      ) : runs.data === undefined ? (
        <p className="text-muted">Loading runs...</p>
      ) : runs.data.runs.length === 0 ? (
        <p className="text-muted">No runs match these filters.</p>
      ) : (
        <>
          <RunsTable runs={runs.data.runs} />
          <p className="font-mono text-xs text-muted tabular-nums">
            {runs.data.runs.length} of {runs.data.total}
          </p>
        </>
      )}
    </section>
  );
}
