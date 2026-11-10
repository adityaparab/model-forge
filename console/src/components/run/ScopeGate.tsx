import { useState } from "react";

import { DecisionForm, type Decide, type Draft } from "./DecisionForm";
import { Fact, Facts } from "./GateFacts";
import { changedFields, scopeSummary, type ModelingPlan } from "./gateSummary";
import { PlanEditor } from "./PlanEditor";

const DECISIONS = ["approve", "edit", "reject"] as const;

/** The scope gate: the proposed plan (editable), its rationale and assumptions. */
export function ScopeGate({
  summary,
  onDecide,
}: {
  summary: Record<string, unknown>;
  onDecide: Decide;
}) {
  const { plan: proposal, rationale, assumptions } = scopeSummary(summary);
  const [decision, setDecision] = useState<string>("approve");
  const [plan, setPlan] = useState<ModelingPlan | null>(proposal);
  const edited = changedFields(proposal, plan).length > 0;

  let draft: Draft = { body: { gate: "scope", decision } };
  if (decision === "edit") {
    draft =
      plan === null
        ? { blocked: "Fix the plan's JSON first." }
        : !edited
          ? { blocked: "Edit the plan, or approve it as proposed." }
          : { body: { gate: "scope", decision, plan } };
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div className="flex flex-col gap-3">
        <Facts>
          <Fact label="model">
            <span className="font-mono">{proposal?.model_name ?? "-"}</span>
          </Fact>
          <Fact label="tier">
            <span className="font-mono">{proposal?.tier ?? "-"}</span>
          </Fact>
          <Fact label="intended use">{proposal?.intended_use ?? "-"}</Fact>
        </Facts>
        {rationale !== null && (
          <div className="flex flex-col gap-1">
            <h3 className="text-xs text-muted">Rationale</h3>
            <p className="text-sm leading-6">{rationale}</p>
          </div>
        )}
        {assumptions.length > 0 && (
          <div className="flex flex-col gap-1">
            <h3 className="text-xs text-muted">Assumptions</h3>
            <ul className="list-disc pl-5 text-sm leading-6">
              {assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </div>
        )}
        <DecisionForm
          decisions={DECISIONS}
          decision={decision}
          onDecision={setDecision}
          draft={draft}
          onDecide={onDecide}
        >
          {edited && decision !== "edit" && (
            <p className="text-xs text-status-awaiting">
              The plan has edits; they are sent only with the edit decision.
            </p>
          )}
        </DecisionForm>
      </div>
      <PlanEditor proposal={proposal} onChange={setPlan} />
    </div>
  );
}
