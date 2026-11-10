import { useState } from "react";

import { Mono } from "../Mono";
import { SeverityPill } from "../SeverityPill";
import { DecisionForm, type Decide, type Draft } from "./DecisionForm";
import { CountList, Fact, Facts, Outcome } from "./GateFacts";
import { isSeverity, signoffSummary } from "./gateSummary";

const DECISIONS = ["approve", "approve_with_conditions", "reject"] as const;

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** The sign-off gate: findings, responses, completeness and the validation outcome. */
export function SignoffGate({
  summary,
  onDecide,
}: {
  summary: Record<string, unknown>;
  onDecide: Decide;
}) {
  const { findings, responses, completeness, validationOverall } = signoffSummary(summary);
  const [decision, setDecision] = useState<string>("approve");
  const [conditions, setConditions] = useState("");

  let draft: Draft = { body: { gate: "signoff", decision } };
  if (decision === "approve_with_conditions") {
    const list = lines(conditions);
    draft =
      list.length === 0
        ? { blocked: "Add at least one condition." }
        : { body: { gate: "signoff", decision, conditions: list } };
  }

  const severity = findings.maxOpenSeverity;
  return (
    <div className="flex flex-col gap-4">
      <Facts>
        <Fact label="max open severity">
          {isSeverity(severity) ? (
            <SeverityPill severity={severity} />
          ) : (
            <span className="text-muted">none open</span>
          )}
        </Fact>
        <Fact label="findings">
          <span className="inline-flex flex-wrap items-center gap-x-4">
            <Mono>{String(findings.n)}</Mono>
            <CountList counts={findings.byStatus} />
          </span>
        </Fact>
        <Fact label="by severity">
          <CountList counts={findings.bySeverity} />
        </Fact>
        <Fact label="remediation rounds">
          <Mono>{String(findings.rounds)}</Mono>
        </Fact>
        <Fact label="responses">
          {responses === null ? (
            <span className="text-muted">none (review only)</span>
          ) : (
            <span className="font-mono text-[0.8125rem] tabular-nums">
              {responses.n} <span className="text-muted">accepted</span> {responses.accepted}{" "}
              <span className="text-muted">disputed</span> {responses.disputed}{" "}
              <span className="text-muted">sections regenerated</span> {responses.regenerated}
            </span>
          )}
        </Fact>
        <Fact label="document">
          {completeness === null ? (
            <span className="text-muted">-</span>
          ) : completeness.complete ? (
            <span className="text-status-completed">complete</span>
          ) : (
            <span className="text-status-attention">
              incomplete: {completeness.missingSections.join(", ") || "unresolved markers"}
            </span>
          )}
        </Fact>
        <Fact label="validation">
          <Outcome value={validationOverall} />
        </Fact>
      </Facts>
      <DecisionForm
        decisions={DECISIONS}
        decision={decision}
        onDecision={setDecision}
        draft={draft}
        onDecide={onDecide}
      >
        {decision === "approve_with_conditions" && (
          <label className="flex flex-col gap-1 text-xs text-muted">
            Conditions (one per line)
            <textarea
              value={conditions}
              onChange={(event) => setConditions(event.target.value)}
              rows={3}
              spellCheck={false}
              className="rounded-sm border border-border bg-bg px-2 py-1 font-mono text-[0.8125rem] text-text focus:border-accent focus:outline-none"
            />
          </label>
        )}
      </DecisionForm>
    </div>
  );
}
