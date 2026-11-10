import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { useBriefs, useSettings, useSubmitRun } from "../api/queries";

const field =
  "rounded-sm border border-border bg-panel-2 px-2 text-sm text-text focus:border-accent focus:outline-none";

/** Submit a run: a brief (typed, or filled from an example), a profile and a cost cap. */
export function NewRunPage() {
  const navigate = useNavigate();
  const briefs = useBriefs();
  const settings = useSettings();
  const submit = useSubmitRun();

  const [example, setExample] = useState("");
  const [brief, setBrief] = useState("");
  // null until the user picks one: the server-side defaults show through until then
  const [profile, setProfile] = useState<string | null>(null);
  const [costCap, setCostCap] = useState<string | null>(null);

  const editable = settings.data?.editable;
  const shownProfile = profile ?? editable?.default_profile ?? "";
  const shownCostCap =
    costCap ?? (editable === undefined ? "" : String(editable.default_cost_cap_usd));

  function onExample(name: string) {
    setExample(name);
    const chosen = briefs.data?.find((item) => item.name === name);
    if (chosen !== undefined) setBrief(chosen.text);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit.mutate(
      {
        brief,
        profile: shownProfile === "" ? undefined : shownProfile,
        cost_cap_usd: shownCostCap === "" ? undefined : Number(shownCostCap),
      },
      { onSuccess: (run) => navigate(`/runs/${encodeURIComponent(run.id)}`) },
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex max-w-4xl flex-col gap-3">
      <h1 className="text-base font-semibold">New run</h1>
      <div className="flex flex-wrap items-end gap-4 text-xs text-muted">
        <label className="flex flex-col gap-1">
          Example
          <select
            aria-label="Example"
            value={example}
            onChange={(event) => onExample(event.target.value)}
            className={`${field} h-7`}
          >
            <option value="">none</option>
            {briefs.data?.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Profile
          <select
            aria-label="Profile"
            value={shownProfile}
            onChange={(event) => setProfile(event.target.value)}
            className={`${field} h-7 font-mono`}
          >
            {settings.data?.profiles.map(({ name }) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Cost cap (USD)
          <input
            aria-label="Cost cap (USD)"
            type="number"
            min={0}
            step={0.01}
            value={shownCostCap}
            onChange={(event) => setCostCap(event.target.value)}
            className={`${field} h-7 w-28 font-mono tabular-nums`}
          />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Brief (YAML)
        <textarea
          aria-label="Brief (YAML)"
          value={brief}
          onChange={(event) => setBrief(event.target.value)}
          spellCheck={false}
          rows={24}
          className={`${field} py-2 font-mono text-[0.8125rem] leading-5`}
        />
      </label>
      {submit.isError && (
        <p
          role="alert"
          className="rounded-sm border border-status-attention bg-status-attention/10 px-3 py-2 text-sm text-text"
        >
          {submit.error.message}
        </p>
      )}
      <div>
        <button
          type="submit"
          disabled={submit.isPending || brief.trim() === ""}
          className="inline-flex h-7 items-center rounded-sm bg-accent px-3 text-sm font-medium text-bg hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
        >
          {submit.isPending ? "Submitting..." : "Submit run"}
        </button>
      </div>
    </form>
  );
}
