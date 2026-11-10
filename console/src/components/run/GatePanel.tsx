import { useId } from "react";

import type { Schema } from "../../api/client";
import type { Decide } from "./DecisionForm";
import { ModelGate } from "./ModelGate";
import { ScopeGate } from "./ScopeGate";
import { SignoffGate } from "./SignoffGate";

const TITLES: Record<string, string> = {
  scope: "Scope gate",
  model: "Model gate",
  signoff: "Sign-off gate",
};

const PROMPTS: Record<string, string> = {
  scope: "Review the proposed modelling plan before any data work starts.",
  model: "Review the champion's validation before the document is drafted.",
  signoff: "Review the challenge outcome and the document before the run is sealed.",
};

/** The human approval gate the run is parked at: the summary and the decision form. */
export function GatePanel({
  detail,
  onDecide,
}: {
  detail: Schema<"RunDetail">;
  onDecide: Decide;
}) {
  const id = useId();
  const gate = detail.gate;
  if (gate === null) return null;
  const title = TITLES[gate.gate] ?? `${gate.gate} gate`;

  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-4 rounded-sm border border-status-awaiting/60 bg-panel p-4"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border pb-3">
        <div className="flex items-baseline gap-3">
          <h2 id={id} className="text-base font-semibold">
            {title}
          </h2>
          <span className="text-xs font-medium tracking-wide text-status-awaiting uppercase">
            awaiting decision
          </span>
        </div>
        <p className="text-xs text-muted">{PROMPTS[gate.gate]}</p>
      </header>
      {gate.gate === "scope" ? (
        <ScopeGate summary={gate.summary} onDecide={onDecide} />
      ) : gate.gate === "model" ? (
        <ModelGate
          runId={detail.run.id}
          planRef={detail.run.plan_ref}
          summary={gate.summary}
          onDecide={onDecide}
        />
      ) : gate.gate === "signoff" ? (
        <SignoffGate summary={gate.summary} onDecide={onDecide} />
      ) : (
        <p className="text-muted">This console does not know the {gate.gate} gate.</p>
      )}
    </section>
  );
}
