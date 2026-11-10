import { useId, useState } from "react";

import { asPlan, changedFields, type ModelingPlan } from "./gateSummary";

function format(plan: ModelingPlan | null): string {
  return plan === null ? "" : JSON.stringify(plan, null, 2);
}

function parse(text: string): { plan: ModelingPlan | null; error: string | null } {
  try {
    const plan = asPlan(JSON.parse(text) as unknown);
    return plan === null
      ? { plan: null, error: "The plan must be a JSON object." }
      : { plan, error: null };
  } catch (error) {
    return { plan: null, error: error instanceof Error ? error.message : String(error) };
  }
}

/**
 * The modelling plan as a monospace JSON textarea, seeded from `proposal`. Every edit is parsed:
 * `onChange` receives the plan, or `null` while the text is not a JSON object, and the parse
 * error shows inline. The fields that differ from the proposal are listed under the editor.
 */
export function PlanEditor({
  proposal,
  onChange,
}: {
  proposal: ModelingPlan | null;
  onChange: (plan: ModelingPlan | null) => void;
}) {
  const id = useId();
  const [text, setText] = useState(() => format(proposal));
  const { plan, error } = parse(text);
  const changed = changedFields(proposal, plan);

  function onEdit(next: string) {
    setText(next);
    onChange(parse(next).plan);
  }

  function onReset() {
    setText(format(proposal));
    onChange(proposal);
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-xs text-muted">
        <label htmlFor={id}>Plan (JSON)</label>
        <button
          type="button"
          onClick={onReset}
          disabled={changed.length === 0 && error === null}
          className="rounded-sm px-1 hover:text-text focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40"
        >
          Reset to proposal
        </button>
      </div>
      <textarea
        id={id}
        value={text}
        onChange={(event) => onEdit(event.target.value)}
        spellCheck={false}
        rows={18}
        aria-invalid={error !== null}
        aria-describedby={`${id}-state`}
        className={`rounded-sm border bg-bg px-2 py-2 font-mono text-[0.8125rem] leading-5 text-text focus:outline-none ${
          error === null ? "border-border focus:border-accent" : "border-status-attention"
        }`}
      />
      <p id={`${id}-state`} className="min-h-5 font-mono text-xs">
        {error !== null ? (
          <span className="text-status-attention">Invalid JSON: {error}</span>
        ) : changed.length === 0 ? (
          <span className="text-muted">unchanged from the proposal</span>
        ) : (
          <span className="text-muted">
            changed:{" "}
            {changed.map((field, index) => (
              <span key={field}>
                {index > 0 && ", "}
                <span className="text-accent">{field}</span>
              </span>
            ))}
          </span>
        )}
      </p>
    </div>
  );
}
