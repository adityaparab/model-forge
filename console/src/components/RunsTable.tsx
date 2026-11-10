import { Link } from "react-router";

import type { Schema } from "../api/client";
import { Mono } from "./Mono";
import { SeverityPill, type Severity } from "./SeverityPill";
import { StatusPill } from "./StatusPill";

type RunListItem = Schema<"RunListItem">;

const SEVERITIES: readonly string[] = ["critical", "high", "medium", "low"] satisfies Severity[];

function isSeverity(value: string | null): value is Severity {
  return value !== null && SEVERITIES.includes(value);
}

const HEAD = [
  "Run",
  "Model",
  "Status",
  "Profile",
  "Requester",
  "Created",
  "Cost (USD)",
  "Findings",
];

const cell = "h-8 border-b border-border px-2 whitespace-nowrap";

function Findings({ findings }: { findings: RunListItem["findings"] }) {
  const severity = findings.max_open_severity;
  return (
    <span className="inline-flex items-center gap-2">
      <Mono>{String(findings.n)}</Mono>
      {isSeverity(severity) && <SeverityPill severity={severity} />}
    </span>
  );
}

/** The run list: one dense 32 px row per run, newest first as the server orders them. */
export function RunsTable({ runs }: { runs: RunListItem[] }) {
  return (
    <table aria-label="Runs" className="w-full border-collapse border border-border text-left">
      <thead className="bg-panel text-xs text-muted">
        <tr>
          {HEAD.map((label) => (
            <th key={label} scope="col" className={`${cell} font-medium`}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.id} className="hover:bg-panel-2">
            <td className={cell}>
              <span className="inline-flex items-center gap-2">
                <Link
                  to={`/runs/${encodeURIComponent(run.id)}`}
                  className="text-text hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <Mono>{run.id}</Mono>
                </Link>
                {run.replay_of !== null && (
                  <span
                    title={`replay of ${run.replay_of}`}
                    className="inline-flex h-5 items-center rounded-sm border border-border px-1.5 text-[0.6875rem] text-muted"
                  >
                    replay
                  </span>
                )}
              </span>
            </td>
            <td className={cell}>{run.model_name ?? <span className="text-muted">-</span>}</td>
            <td className={cell}>
              <StatusPill status={run.status} />
            </td>
            <td className={cell}>{run.profile}</td>
            <td className={cell}>{run.requester}</td>
            <td className={cell}>
              <time dateTime={run.created_at} className="font-mono text-[0.8125rem] tabular-nums">
                {new Date(run.created_at).toLocaleString()}
              </time>
            </td>
            <td className={cell}>
              <Mono>{`${run.cost_usd.toFixed(2)} / ${run.cost_cap_usd.toFixed(2)}`}</Mono>
            </td>
            <td className={cell}>
              <Findings findings={run.findings} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
