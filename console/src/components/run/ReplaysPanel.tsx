import { Link } from "react-router";

import type { Schema } from "../../api/client";
import { Mono } from "../Mono";
import { StatusPill } from "../StatusPill";

type ReplayItem = Schema<"ReplayItem">;

const SETTLED: readonly string[] = ["completed", "rejected", "aborted", "needs_attention"];

type Badge = { text: string; tone: "ok" | "bad" | "neutral" };

/** The reproducibility verdict of the latest replay. */
function badge(latest: ReplayItem): Badge {
  const { run, report } = latest;
  if (!SETTLED.includes(run.status) || report === null) {
    return { text: `Replay ${run.status.replaceAll("_", " ")}`, tone: "neutral" };
  }
  if (!report.ok) {
    const refs = [...report.mismatched.map((item) => item.ref), ...report.missing];
    return { text: `Mismatch: ${refs.join(", ") || "see report"}`, tone: "bad" };
  }
  if (run.rerun_llm) {
    return {
      text: `Replay with --rerun-llm matched ${report.matched.length} artifacts`,
      tone: "neutral",
    };
  }
  return { text: `Reproduced: ${report.matched.length} artifacts matched`, tone: "ok" };
}

const TONE: Record<Badge["tone"], string> = {
  ok: "border-status-completed text-status-completed",
  bad: "border-status-attention text-status-attention",
  neutral: "border-border text-muted",
};

const cell = "h-8 border-b border-border px-2 whitespace-nowrap";

/** The run's replays (oldest first, as the server lists them) and whether they reproduced it. */
export function ReplaysPanel({ replays }: { replays: ReplayItem[] }) {
  const latest = replays.at(-1);
  const verdict = latest === undefined ? null : badge(latest);
  return (
    <section aria-label="Replays" className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold">Replays</h2>
        {verdict !== null && (
          <span
            role="status"
            aria-label="Reproducibility"
            className={`inline-flex h-6 items-center rounded-sm border px-2 text-xs font-medium ${TONE[verdict.tone]}`}
          >
            {verdict.text}
          </span>
        )}
      </div>
      {replays.length === 0 ? (
        <p className="text-xs text-muted">No replays yet.</p>
      ) : (
        <table
          aria-label="Replay runs"
          className="w-full border-collapse border border-border text-left"
        >
          <thead className="bg-panel text-xs text-muted">
            <tr>
              {["Replay", "Mode", "Status", "Matched", "Regenerated"].map((head) => (
                <th key={head} scope="col" className={`${cell} font-medium`}>
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {replays.map(({ run, report }) => (
              <tr key={run.id}>
                <td className={cell}>
                  <Link
                    to={`/runs/${encodeURIComponent(run.id)}`}
                    className="text-text hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <Mono>{run.id}</Mono>
                  </Link>
                </td>
                <td className={`${cell} font-mono text-[0.8125rem]`}>
                  {run.rerun_llm ? "--rerun-llm" : "plain"}
                </td>
                <td className={cell}>
                  <StatusPill status={run.status} />
                </td>
                <td className={`${cell} font-mono text-[0.8125rem] tabular-nums`}>
                  {report === null ? "-" : report.matched.length}
                </td>
                <td className={`${cell} font-mono text-[0.8125rem] whitespace-normal`}>
                  {report === null || (report.regenerated ?? []).length === 0
                    ? "-"
                    : (report.regenerated ?? []).join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
