import type { Schema } from "../../api/client";

type StepDetail = Schema<"StepDetail">;
type NodeStatus = StepDetail["status"] | "not started";

const STATUS_CLASSES: Record<NodeStatus, string> = {
  "not started": "text-muted",
  running: "text-status-running",
  succeeded: "text-status-completed",
  failed: "text-status-attention",
};

type NodeRow = {
  node: string;
  status: NodeStatus;
  attempts: number;
  durationMs: number | null;
  toolCalls: number;
  llmCalls: number;
};

/** One row per node: the latest attempt's status and duration, the calls over every attempt. */
function rows(steps: StepDetail[], nodes: string[]): NodeRow[] {
  return nodes.map((node) => {
    const attempts = steps.filter((step) => step.node === node);
    const latest = attempts.reduce<StepDetail | undefined>(
      (best, step) => (best === undefined || step.attempt >= best.attempt ? step : best),
      undefined,
    );
    return {
      node,
      status: latest?.status ?? "not started",
      attempts: attempts.length,
      durationMs: latest?.duration_ms ?? null,
      toolCalls: attempts.reduce((n, step) => n + step.tool_calls.length, 0),
      llmCalls: attempts.reduce((n, step) => n + step.llm_calls.length, 0),
    };
  });
}

const HEAD = ["Node", "Status", "Attempts", "Duration", "Tool calls", "LLM calls"];
const cell = "h-8 border-b border-border px-2 whitespace-nowrap";
const num = `${cell} text-right font-mono text-[0.8125rem] tabular-nums`;

/** The run's graph, node by node in graph order, with what each node's attempts did. */
export function Timeline({ steps, nodes }: { steps: StepDetail[]; nodes: string[] }) {
  return (
    <table aria-label="Timeline" className="w-full border-collapse border border-border text-left">
      <thead className="bg-panel text-xs text-muted">
        <tr>
          {HEAD.map((label, index) => (
            <th
              key={label}
              scope="col"
              className={`${cell} font-medium ${index >= 2 ? "text-right" : ""}`}
            >
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows(steps, nodes).map((row) => (
          <tr key={row.node} className={row.status === "not started" ? "text-muted" : ""}>
            <th scope="row" className={`${cell} font-mono text-[0.8125rem] font-normal`}>
              {row.node}
            </th>
            <td className={cell}>
              <span className={`inline-flex items-center gap-2 ${STATUS_CLASSES[row.status]}`}>
                <span
                  aria-hidden="true"
                  className={`size-1.5 rounded-full bg-current ${row.status === "running" ? "animate-pulse" : ""}`}
                />
                {row.status}
              </span>
            </td>
            <td className={num}>{row.attempts}</td>
            <td className={num}>{row.durationMs === null ? "-" : `${row.durationMs} ms`}</td>
            <td className={num}>{row.toolCalls}</td>
            <td className={num}>{row.llmCalls}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
