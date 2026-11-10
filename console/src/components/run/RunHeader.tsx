import type { ReactNode } from "react";
import { NavLink } from "react-router";

import type { Schema } from "../../api/client";
import { usePause, useReplay } from "../../api/queries";
import { Mono } from "../Mono";
import { StatusPill } from "../StatusPill";
import {
  AbortAction,
  ActionError,
  ResumeAction,
  primaryButton,
  secondaryButton,
} from "./RunActions";
import { scopeSummary } from "./gateSummary";

type RunDetail = Schema<"RunDetail">;

const TABS = [
  { to: "", label: "Overview" },
  { to: "document", label: "Document" },
  { to: "validation", label: "Validation" },
  { to: "findings", label: "Findings" },
  { to: "lineage", label: "Lineage" },
  { to: "llm-calls", label: "LLM calls" },
] as const;

/** The model's name: the inventory entry's once sealed, else the plan at the scope gate. */
function modelName(detail: RunDetail): string | null {
  if (detail.inventory !== null) return detail.inventory.name;
  if (detail.gate?.gate !== "scope") return null;
  return scopeSummary(detail.gate.summary).plan?.model_name ?? null;
}

function PauseAction({ run }: { run: RunDetail["run"] }) {
  const pause = usePause(run.id);
  const requested = run.pause_requested || pause.isSuccess;
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        disabled={requested || pause.isPending}
        onClick={() => pause.mutate()}
        className={secondaryButton}
      >
        {requested ? "Pause requested" : "Pause"}
      </button>
      <ActionError error={pause.error} />
    </span>
  );
}

function ReplayActions({ runId }: { runId: string }) {
  const replay = useReplay(runId);
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        disabled={replay.isPending}
        onClick={() => replay.mutate({ rerun_llm: false })}
        className={primaryButton}
      >
        Replay
      </button>
      <button
        type="button"
        disabled={replay.isPending}
        onClick={() => replay.mutate({ rerun_llm: true })}
        className={secondaryButton}
      >
        Replay with --rerun-llm
      </button>
      {replay.isSuccess && (
        <span role="status" className="text-xs text-muted">
          replay <Mono>{replay.data.id}</Mono> queued
        </span>
      )}
      <ActionError error={replay.error} />
    </span>
  );
}

/** The actions the run's status allows. */
function Actions({ run }: { run: RunDetail["run"] }) {
  switch (run.status) {
    case "running":
      return <PauseAction run={run} />;
    case "needs_attention":
    case "paused":
      return (
        <>
          <ResumeAction runId={run.id} />
          <AbortAction runId={run.id} />
        </>
      );
    case "queued":
    case "awaiting_scope":
    case "awaiting_model":
    case "awaiting_signoff":
      return <AbortAction runId={run.id} />;
    case "completed":
    case "rejected":
      return <ReplayActions runId={run.id} />;
    case "aborted":
      return null;
  }
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <dt className="text-xs text-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function TraceLink({ href, label }: { href: string | null; label: string }) {
  if (href === null) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-xs text-muted underline decoration-border underline-offset-4 hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
    >
      {label}
    </a>
  );
}

/** The run's identity, cost and trace links, the actions its status allows, and the tabs. */
export function RunHeader({ detail }: { detail: RunDetail }) {
  const { run, trace } = detail;
  const name = modelName(detail);
  const base = `/runs/${encodeURIComponent(run.id)}`;

  return (
    <section aria-label="Run" className="flex flex-col gap-3 border-b border-border">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-base font-semibold">{run.id}</h1>
          <StatusPill status={run.status} />
          {run.replay_of !== null && (
            <span className="text-xs text-muted">
              replay of{" "}
              <NavLink
                to={`/runs/${encodeURIComponent(run.replay_of)}`}
                className="text-text hover:text-accent"
              >
                <Mono>{run.replay_of}</Mono>
              </NavLink>
              {run.rerun_llm && " (--rerun-llm)"}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Actions run={run} />
        </div>
      </div>
      <dl className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        <Meta label="model">{name ?? <span className="text-muted">-</span>}</Meta>
        <Meta label="profile">
          <Mono>{run.profile}</Mono>
        </Meta>
        <Meta label="requester">{run.requester}</Meta>
        <Meta label="cost (USD)">
          <Mono>{`${run.cost_usd.toFixed(2)} / ${run.cost_cap_usd.toFixed(2)}`}</Mono>
        </Meta>
        {(trace.jaeger_url !== null || trace.phoenix_url !== null) && (
          <div className="flex items-center gap-3">
            <dt className="text-xs text-muted">trace</dt>
            <dd className="flex items-center gap-3">
              <TraceLink href={trace.jaeger_url} label="Jaeger" />
              <TraceLink href={trace.phoenix_url} label="Phoenix" />
            </dd>
          </div>
        )}
      </dl>
      <nav aria-label="Run tabs" className="-mb-px flex gap-1">
        {TABS.map(({ to, label }) => (
          <NavLink
            key={label}
            to={to === "" ? base : `${base}/${to}`}
            end
            className={({ isActive }) =>
              `flex h-8 items-center border-b-2 px-3 text-sm focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent ${
                isActive
                  ? "border-accent text-text"
                  : "border-transparent text-muted hover:text-text"
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
    </section>
  );
}
