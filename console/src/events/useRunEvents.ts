import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { API_PREFIX, type Schema } from "../api/client";
import { runKeys } from "../api/queries";
import { setConnectionState, type ConnectionState } from "./connection";

type EventRecord = Schema<"EventRecord">;

/**
 * The event types the run stream carries. The server names each message (`event: <type>`), and
 * `EventSource` only hands a named message to a listener registered for that name.
 */
export const RUN_EVENT_TYPES = [
  "run.created",
  "run.enqueued",
  "run.claimed",
  "run.claim_rejected",
  "run.status_changed",
  "run.paused",
  "run.sealed",
  "intake.validated",
  "step.started",
  "step.finished",
  "tool.called",
  "tool.failed",
  "llm.called",
  "llm.failed",
  "artifact.stored",
  "profile.psi_skipped",
  "gate.requested",
  "gate.decided",
  "gate.applied",
  "error.recorded",
  "replay.completed",
] as const;

const DEBOUNCE_MS = 300;
const KEEP = 200;

function parse(data: unknown): EventRecord | null {
  if (typeof data !== "string") return null;
  try {
    const value = JSON.parse(data) as unknown;
    if (typeof value === "object" && value !== null && "id" in value && "type" in value) {
      return value as EventRecord;
    }
  } catch {
    // a malformed message still refreshes the run below
  }
  return null;
}

/**
 * Follows one run's event stream: every message refreshes the run's queries (debounced), the
 * latest 200 events are kept for the live feed, and the connection state is reported.
 */
export function useRunEvents(runId: string): { events: EventRecord[]; state: ConnectionState } {
  const client = useQueryClient();
  // kept per run, so a page that switches runs never shows the previous run's events
  const [feed, setFeed] = useState<{ runId: string; events: EventRecord[] }>({
    runId,
    events: [],
  });
  const [state, setState] = useState<ConnectionState>("offline");

  useEffect(() => {
    if (runId === "") return;
    const source = new EventSource(
      `${API_PREFIX}/runs/${encodeURIComponent(runId)}/events/stream`,
    );
    let timer: number | undefined;

    const report = (next: ConnectionState) => {
      setState(next);
      setConnectionState(next);
    };

    const onMessage = (message: MessageEvent) => {
      const event = parse(message.data);
      if (event !== null) {
        setFeed((current) => {
          const kept = current.runId === runId ? current.events : [];
          // a reconnect resumes after the last id, so a repeat is only ever a duplicate
          if (kept.length > 0 && kept[kept.length - 1].id >= event.id) return current;
          return { runId, events: [...kept, event].slice(-KEEP) };
        });
      }
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        void client.invalidateQueries({ queryKey: runKeys.run(runId) });
      }, DEBOUNCE_MS);
    };

    source.onopen = () => report("live");
    source.onerror = () =>
      report(source.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
    source.onmessage = onMessage;
    RUN_EVENT_TYPES.forEach((type) => source.addEventListener(type, onMessage));

    return () => {
      window.clearTimeout(timer);
      RUN_EVENT_TYPES.forEach((type) => source.removeEventListener(type, onMessage));
      source.close();
      setConnectionState("offline");
    };
  }, [client, runId]);

  return { events: feed.runId === runId ? feed.events : [], state };
}
