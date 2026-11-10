import type { Schema } from "../../api/client";

type ApprovalRecord = Schema<"ApprovalRecord">;

const HEAD = ["Gate", "Decision", "Approver", "Time", "Details"];
const cell = "h-8 border-b border-border px-2";

function details(approval: ApprovalRecord): string[] {
  const parts: string[] = [];
  if (approval.adjust_target !== null) parts.push(`back to ${approval.adjust_target}`);
  if (approval.plan_diff !== null && approval.plan_diff.length > 0) {
    parts.push(`plan: ${approval.plan_diff.map((entry) => entry.path).join(", ")}`);
  }
  if (approval.conditions !== null && approval.conditions.length > 0) {
    parts.push(`conditions: ${approval.conditions.join("; ")}`);
  }
  if (approval.comment !== "") parts.push(`"${approval.comment}"`);
  return parts;
}

/** Every recorded gate decision, in the order they were made. */
export function ApprovalsList({ approvals }: { approvals: ApprovalRecord[] }) {
  return (
    <section aria-label="Approvals list" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Approvals</h2>
      {approvals.length === 0 ? (
        <p className="text-xs text-muted">No gate decisions yet.</p>
      ) : (
        <table
          aria-label="Approvals"
          className="w-full border-collapse border border-border text-left"
        >
          <thead className="bg-panel text-xs text-muted">
            <tr>
              {HEAD.map((head) => (
                <th key={head} scope="col" className={`${cell} font-medium whitespace-nowrap`}>
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {approvals.map((approval) => (
              <tr key={approval.id}>
                <td className={`${cell} font-mono text-[0.8125rem]`}>{approval.gate}</td>
                <td
                  className={`${cell} ${approval.decision === "reject" ? "text-status-rejected" : ""}`}
                >
                  {approval.decision.replaceAll("_", " ")}
                </td>
                <td className={cell}>{approval.approver}</td>
                <td className={`${cell} whitespace-nowrap`}>
                  <time dateTime={approval.ts} className="font-mono text-[0.8125rem] tabular-nums">
                    {new Date(approval.ts).toLocaleString()}
                  </time>
                </td>
                <td className={`${cell} text-xs text-muted`}>
                  {details(approval).join(" · ") || "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
