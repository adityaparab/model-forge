import { useState, type FormEvent, type ReactNode } from "react";

import { ApiError } from "../../api/client";
import type { ApproveBody } from "../../api/queries";
import { SeverityPill } from "../SeverityPill";
import { blockingFindings, isSeverity } from "./gateSummary";

export type Decide = (body: ApproveBody) => Promise<unknown>;

/** What the gate's own fields make of the chosen decision: a body to send, or why not yet. */
export type Draft = { body: Omit<ApproveBody, "comment"> } | { blocked: string };

const label = (decision: string) => decision.replaceAll("_", " ");

function Refusal({ error }: { error: Error }) {
  const findings = error instanceof ApiError ? blockingFindings(error.extra) : [];
  const refused = error instanceof ApiError && error.status === 409;
  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-sm border border-status-attention bg-status-attention/10 px-3 py-2"
    >
      <p className="text-sm">
        <span className="font-semibold text-status-attention">
          {refused ? "Decision refused" : "Decision not recorded"}
        </span>
        <span className="text-muted"> · </span>
        {error.message}
      </p>
      {findings.length > 0 && (
        <table aria-label="Blocking findings" className="w-full border-collapse text-left">
          <thead className="text-xs text-muted">
            <tr>
              {["Finding", "Severity", "Title"].map((head) => (
                <th key={head} scope="col" className="h-7 border-b border-border pr-4 font-medium">
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {findings.map((finding) => (
              <tr key={finding.id}>
                <td className="h-8 border-b border-border pr-4 font-mono text-[0.8125rem]">
                  {finding.id}
                </td>
                <td className="h-8 border-b border-border pr-4">
                  {isSeverity(finding.severity) ? (
                    <SeverityPill severity={finding.severity} />
                  ) : (
                    finding.severity
                  )}
                </td>
                <td className="h-8 border-b border-border">{finding.title}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/**
 * The decision half of a gate panel: the gate's decisions as a segmented choice, the fields the
 * chosen decision needs (`children`), a comment, and the primary submit. A failed decision (a
 * `409` refusal with its blocking findings, a four-eyes refusal, any other error) shows under
 * the form until the next attempt.
 */
export function DecisionForm({
  decisions,
  decision,
  onDecision,
  draft,
  onDecide,
  children,
}: {
  decisions: readonly string[];
  decision: string;
  onDecision: (decision: string) => void;
  draft: Draft;
  onDecide: Decide;
  children?: ReactNode;
}) {
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [sent, setSent] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!("body" in draft)) return;
    const trimmed = comment.trim();
    setPending(true);
    setError(null);
    setSent(null);
    try {
      await onDecide(trimmed === "" ? draft.body : { ...draft.body, comment: trimmed });
      setSent(draft.body.decision);
    } catch (failure) {
      setError(failure instanceof Error ? failure : new Error(String(failure)));
    } finally {
      setPending(false);
    }
  }

  const blocked = "blocked" in draft ? draft.blocked : null;

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3">
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-xs text-muted">Decision</legend>
        <div className="inline-flex w-fit overflow-hidden rounded-sm border border-border">
          {decisions.map((option) => (
            <label
              key={option}
              className={`flex h-8 cursor-pointer items-center gap-2 border-r border-border px-3 text-sm last:border-r-0 has-[:focus-visible]:outline-2 has-[:focus-visible]:-outline-offset-2 has-[:focus-visible]:outline-accent ${
                option === decision
                  ? option === "reject"
                    ? "bg-status-rejected/25 text-text"
                    : "bg-accent/15 text-accent"
                  : "bg-panel-2 text-muted hover:text-text"
              }`}
            >
              <input
                type="radio"
                name="decision"
                value={option}
                checked={option === decision}
                onChange={() => onDecision(option)}
                className="sr-only"
              />
              {label(option)}
            </label>
          ))}
        </div>
      </fieldset>
      {children}
      <label className="flex flex-col gap-1 text-xs text-muted">
        Comment
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          rows={2}
          className="rounded-sm border border-border bg-bg px-2 py-1 text-sm text-text focus:border-accent focus:outline-none"
        />
      </label>
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={pending || blocked !== null}
          className="inline-flex h-8 items-center rounded-sm bg-accent px-4 text-sm font-semibold text-bg hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
        >
          {pending ? "Recording..." : `Record decision: ${label(decision)}`}
        </button>
        {blocked !== null && <span className="text-xs text-muted">{blocked}</span>}
        {sent !== null && error === null && (
          <span role="status" className="text-xs text-muted">
            {label(sent)} recorded; the run resumes.
          </span>
        )}
      </div>
      {error !== null && <Refusal error={error} />}
    </form>
  );
}
