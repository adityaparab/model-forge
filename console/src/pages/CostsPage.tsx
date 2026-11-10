import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { useCosts } from "../api/queries";
import type { Schema } from "../api/client";
import { Bars } from "../components/Bars";
import { Mono } from "../components/Mono";
import { StatusPill } from "../components/StatusPill";

type CostGroup = Schema<"CostGroup">;
type CostRunRow = Schema<"CostRunRow">;

const WINDOWS = [7, 30, 90] as const;

const count = new Intl.NumberFormat("en-US");
const usd = (value: number, digits = 2) => `$${value.toFixed(digits)}`;

const head = "h-8 border-b border-border px-2 text-left text-xs font-medium whitespace-nowrap text-muted";
const cell = "h-8 border-b border-border px-2 whitespace-nowrap";
const num = `${cell} text-right font-mono text-[0.75rem] tabular-nums`;

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex flex-col gap-1 rounded-sm border border-border bg-panel px-3 py-2">
      <span className="text-xs text-muted">{label}</span>
      <span className="font-mono text-base tabular-nums">{value}</span>
    </li>
  );
}

function RunTable({ runs }: { runs: CostRunRow[] }) {
  return (
    <table aria-label="Cost by run" className="w-full border-collapse border border-border text-[0.8125rem]">
      <thead className="bg-panel">
        <tr>
          <th className={head}>Run</th>
          <th className={head}>Status</th>
          <th className={head}>Profile</th>
          <th className={head}>Requester</th>
          <th className={`${head} text-right`}>Cost / cap</th>
          <th className={`${head} text-right`}>Calls</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.run_id} className="hover:bg-panel">
            <td className={cell}>
              <Link
                to={`/runs/${encodeURIComponent(run.run_id)}`}
                className="text-text hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
              >
                <Mono>{run.run_id}</Mono>
              </Link>
            </td>
            <td className={cell}>
              <StatusPill status={run.status} />
            </td>
            <td className={cell}>{run.profile}</td>
            <td className={cell}>{run.requester}</td>
            <td className={num}>{`${usd(run.cost_usd)} / ${usd(run.cost_cap_usd)}`}</td>
            <td className={num}>{count.format(run.n_calls)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * One grouping of the window's calls. Profile groups count runs and leave tokens at 0; model
 * and purpose groups carry tokens and leave runs at 0, so each shows only its own columns.
 */
function GroupTable({
  label,
  keyLabel,
  groups,
  counts,
}: {
  label: string;
  keyLabel: string;
  groups: CostGroup[];
  counts: "runs" | "tokens";
}) {
  return (
    <table aria-label={label} className="w-full border-collapse border border-border text-[0.8125rem]">
      <thead className="bg-panel">
        <tr>
          <th className={head}>{keyLabel}</th>
          {counts === "runs" && <th className={`${head} text-right`}>Runs</th>}
          <th className={`${head} text-right`}>Calls</th>
          {counts === "tokens" && (
            <>
              <th className={`${head} text-right`}>Input</th>
              <th className={`${head} text-right`}>Output</th>
            </>
          )}
          <th className={`${head} text-right`}>Cost</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((group) => (
          <tr key={group.key} className="hover:bg-panel">
            <td className={`${cell} font-mono text-[0.75rem]`}>{group.key}</td>
            {counts === "runs" && <td className={num}>{count.format(group.n_runs)}</td>}
            <td className={num}>{count.format(group.n_calls)}</td>
            {counts === "tokens" && (
              <>
                <td className={num}>{count.format(group.input_tokens)}</td>
                <td className={num}>{count.format(group.output_tokens)}</td>
              </>
            )}
            <td className={num}>{usd(group.cost_usd, 4)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** Spend across runs over a trailing window of days: totals, spend by day, and its groupings. */
export function CostsPage() {
  const [days, setDays] = useState<number>(30);
  const costs = useCosts(days);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold">Costs</h1>
        <div role="group" aria-label="Window" className="flex">
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              aria-pressed={days === window}
              onClick={() => setDays(window)}
              className={`-ml-px h-7 border border-border px-3 text-xs first:ml-0 focus-visible:outline-2 focus-visible:outline-accent ${
                days === window ? "bg-panel-2 text-text" : "bg-panel text-muted hover:text-text"
              }`}
            >
              {`${window} days`}
            </button>
          ))}
        </div>
      </div>
      {costs.isError ? (
        <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
          Could not load the costs: {costs.error.message}
        </p>
      ) : costs.data === undefined ? (
        <p className="text-muted">Loading the costs...</p>
      ) : (
        <>
          <p className="font-mono text-xs text-muted tabular-nums">
            <time dateTime={costs.data.since}>{costs.data.since}</time>
            {" to "}
            <time dateTime={costs.data.until}>{costs.data.until}</time>
          </p>
          <ul aria-label="Totals" className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <Tile label="Total USD" value={usd(costs.data.total_usd)} />
            <Tile label="Runs" value={count.format(costs.data.n_runs)} />
            <Tile label="Calls" value={count.format(costs.data.n_calls)} />
            <Tile label="Tokens in" value={count.format(costs.data.tokens.input)} />
            <Tile label="Tokens out" value={count.format(costs.data.tokens.output)} />
          </ul>
          <Section title="By day">
            {costs.data.by_day.length === 0 ? (
              <p className="text-muted">No calls in this window.</p>
            ) : (
              <Bars
                label="Cost by day"
                items={costs.data.by_day.map((day) => ({ label: day.day, value: day.cost_usd }))}
                format={(value) => usd(value)}
              />
            )}
          </Section>
          <Section title="By run">
            {costs.data.by_run.length === 0 ? (
              <p className="text-muted">No runs created in this window.</p>
            ) : (
              <RunTable runs={costs.data.by_run} />
            )}
          </Section>
          <div className="grid gap-4 xl:grid-cols-3">
            <Section title="By profile">
              <GroupTable
                label="Cost by profile"
                keyLabel="Profile"
                groups={costs.data.by_profile}
                counts="runs"
              />
            </Section>
            <Section title="By model">
              <GroupTable
                label="Cost by model"
                keyLabel="Model"
                groups={costs.data.by_model}
                counts="tokens"
              />
            </Section>
            <Section title="By purpose">
              <GroupTable
                label="Cost by purpose"
                keyLabel="Purpose"
                groups={costs.data.by_purpose}
                counts="tokens"
              />
            </Section>
          </div>
        </>
      )}
    </section>
  );
}
