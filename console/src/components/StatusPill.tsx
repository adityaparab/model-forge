import type { Schema } from "../api/client";

export type RunStatus = Schema<"RunRecord">["status"];

/** Filled pills: the fill carries the status, the text stays legible on it. */
const STATUS_CLASSES: Record<RunStatus, string> = {
  queued: "bg-status-queued text-text",
  running: "bg-status-running text-text",
  awaiting_scope: "bg-status-awaiting text-bg",
  awaiting_model: "bg-status-awaiting text-bg",
  awaiting_signoff: "bg-status-awaiting text-bg",
  needs_attention: "bg-status-attention text-text",
  paused: "bg-status-paused text-bg",
  completed: "bg-status-completed text-bg",
  rejected: "bg-status-rejected text-text",
  aborted: "bg-status-aborted text-text",
};

export function StatusPill({ status }: { status: RunStatus }) {
  return (
    <span
      className={`inline-flex h-5 items-center rounded-sm px-2 text-xs font-medium whitespace-nowrap ${STATUS_CLASSES[status]}`}
    >
      {status.replaceAll("_", " ")}
    </span>
  );
}
