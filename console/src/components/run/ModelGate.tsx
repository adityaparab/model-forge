import { useState } from "react";

import { usePlan } from "../../api/queries";
import { Mono } from "../Mono";
import { DecisionForm, type Decide, type Draft } from "./DecisionForm";
import { Fact, Facts, Outcome } from "./GateFacts";
import { changedFields, modelSummary, type ModelingPlan } from "./gateSummary";
import { PlanEditor } from "./PlanEditor";

const DECISIONS = ["accept", "adjust", "reject"] as const;
const TARGETS = ["features", "build"] as const;
type Target = (typeof TARGETS)[number];

/** The plan editor for `adjust`, seeded from the run's current plan artifact. */
function AdjustFields({
  runId,
  planRef,
  target,
  onTarget,
  onPlan,
}: {
  runId: string;
  planRef: string | null;
  target: Target;
  onTarget: (target: Target) => void;
  onPlan: (proposal: ModelingPlan | null, plan: ModelingPlan | null) => void;
}) {
  const current = usePlan(runId, planRef);
  return (
    <div className="flex flex-col gap-3">
      <label className="flex w-fit flex-col gap-1 text-xs text-muted">
        Go back to
        <select
          value={target}
          onChange={(event) => onTarget(event.target.value as Target)}
          className="h-8 rounded-sm border border-border bg-panel-2 px-2 font-mono text-sm text-text focus:border-accent focus:outline-none"
        >
          {TARGETS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
      {planRef === null ? (
        <p className="text-xs text-status-attention">The run has no stored plan to adjust.</p>
      ) : current.isError ? (
        <p role="alert" className="text-xs text-status-attention">
          Could not load the plan: {current.error.message}
        </p>
      ) : current.data === undefined ? (
        <p className="text-xs text-muted">Loading the plan...</p>
      ) : (
        <PlanEditor
          key={planRef}
          proposal={current.data}
          onChange={(plan) => onPlan(current.data, plan)}
        />
      )}
    </div>
  );
}

/** The model gate: the validation outcome by test, the selected features and the build loops. */
export function ModelGate({
  runId,
  planRef,
  summary,
  onDecide,
}: {
  runId: string;
  planRef: string | null;
  summary: Record<string, unknown>;
  onDecide: Decide;
}) {
  const { overall, statuses, selected, buildLoops } = modelSummary(summary);
  const [decision, setDecision] = useState<string>("accept");
  const [target, setTarget] = useState<Target>("features");
  const [edit, setEdit] = useState<{ proposal: ModelingPlan | null; plan: ModelingPlan | null }>({
    proposal: null,
    plan: null,
  });

  function onDecision(next: string) {
    // the plan editor unmounts with `adjust` and comes back showing the stored plan, so an
    // earlier edit must not stay behind to be sent unseen
    if (next !== "adjust") setEdit({ proposal: null, plan: null });
    setDecision(next);
  }

  let draft: Draft = { body: { gate: "model", decision } };
  if (decision === "adjust") {
    draft =
      edit.plan === null
        ? { blocked: "Edit the plan to adjust." }
        : changedFields(edit.proposal, edit.plan).length === 0
          ? { blocked: "Change the plan, or accept the model." }
          : { body: { gate: "model", decision, adjust_target: target, plan: edit.plan } };
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex flex-col gap-3">
        <Facts>
          <Fact label="validation">
            <Outcome value={overall} />
          </Fact>
          <Fact label="selected features">
            <span className="inline-flex flex-wrap gap-1">
              {selected.length === 0 ? (
                <span className="text-muted">-</span>
              ) : (
                selected.map((feature) => (
                  <span key={feature} className="rounded-sm border border-border px-1.5">
                    <Mono>{feature}</Mono>
                  </span>
                ))
              )}
            </span>
          </Fact>
          <Fact label="build loops">
            <Mono>{String(buildLoops)}</Mono>
          </Fact>
        </Facts>
        <DecisionForm
          decisions={DECISIONS}
          decision={decision}
          onDecision={onDecision}
          draft={draft}
          onDecide={onDecide}
        >
          {decision === "adjust" && (
            <AdjustFields
              runId={runId}
              planRef={planRef}
              target={target}
              onTarget={setTarget}
              onPlan={(proposal, plan) => setEdit({ proposal, plan })}
            />
          )}
        </DecisionForm>
      </div>
      <table aria-label="Validation tests" className="h-fit w-full border-collapse text-left">
        <thead className="text-xs text-muted">
          <tr>
            <th scope="col" className="h-8 border-b border-border px-2 font-medium">
              Test
            </th>
            <th scope="col" className="h-8 border-b border-border px-2 font-medium">
              Outcome
            </th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(statuses).map(([test, status]) => (
            <tr key={test}>
              <td className="h-8 border-b border-border px-2 font-mono text-[0.8125rem]">{test}</td>
              <td className="h-8 border-b border-border px-2">
                <Outcome value={status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
