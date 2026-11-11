import type { Schema } from "../../api/client";

type RunDetail = Schema<"RunDetail">;

/**
 * The gate panel's identity: a new decision at the same gate (an adjust loop back to it) is a
 * new panel, so the decision form's one-shot `sent` lock never carries over.
 */
export function gatePanelKey(detail: Pick<RunDetail, "run" | "gate" | "approvals">): string {
  return `${detail.run.id}:${detail.gate?.gate ?? ""}:${detail.approvals.length}`;
}
