import { useId } from "react";

import type { Schema } from "../../api/client";
import { Mono } from "../Mono";
import { Fact, Facts } from "./GateFacts";
import { isRecord } from "./gateSummary";
import { AbortAction, ResumeAction } from "./RunActions";

type ErrorView = { kind: string; message: string; node: string; attempt: number | null };

/** The run's `error` (an `ErrorRecord`: `node`, `attempt`, `kind`, `error: {type, message}`). */
function errorView(error: Record<string, unknown> | null): ErrorView {
  const record = error ?? {};
  const inner = isRecord(record.error) ? record.error : {};
  const message =
    typeof inner.message === "string"
      ? inner.message
      : typeof record.message === "string"
        ? record.message
        : "no message recorded";
  return {
    kind: typeof record.kind === "string" ? record.kind : "unknown",
    message: typeof inner.type === "string" ? `${inner.type}: ${message}` : message,
    node: typeof record.node === "string" ? record.node : "-",
    attempt: typeof record.attempt === "number" ? record.attempt : null,
  };
}

/** A run that stopped on an error: what failed, where, and the way out (resume or abort). */
export function AttentionPanel({ run }: { run: Schema<"RunRecord"> }) {
  const id = useId();
  const error = errorView(run.error);
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-3 rounded-sm border border-status-attention/70 bg-panel p-4"
    >
      <h2 id={id} className="text-base font-semibold">
        Needs attention
      </h2>
      <Facts>
        <Fact label="kind">
          <Mono>{error.kind}</Mono>
        </Fact>
        <Fact label="node">
          <Mono>{error.node}</Mono>
          {error.attempt !== null && (
            <span className="text-muted"> attempt {error.attempt}</span>
          )}
        </Fact>
        <Fact label="error">
          <span className="font-mono text-[0.8125rem] break-words">{error.message}</span>
        </Fact>
      </Facts>
      <div className="flex flex-wrap items-center gap-2">
        <ResumeAction runId={run.id} />
        <AbortAction runId={run.id} />
      </div>
    </section>
  );
}
