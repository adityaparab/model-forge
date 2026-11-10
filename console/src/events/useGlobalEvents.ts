import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { API_PREFIX } from "../api/client";
import { runKeys } from "../api/queries";
import { setConnectionState } from "./connection";

/** The event types that change what the run list shows. */
const LIST_EVENTS: readonly string[] = [
  "run.created",
  "run.status_changed",
  "gate.requested",
  "run.sealed",
];

/** The `type` of a frame's `EventRecord` (the server sends unnamed frames). */
function eventType(data: unknown): string | null {
  if (typeof data !== "string") return null;
  try {
    const record = JSON.parse(data) as unknown;
    if (typeof record === "object" && record !== null && "type" in record) {
      return typeof record.type === "string" ? record.type : null;
    }
  } catch {
    // not an event record
  }
  return null;
}

const DEBOUNCE_MS = 300;

/** Keeps every run query fresh from the global event stream and reports the connection state. */
export function useGlobalEvents(): void {
  const client = useQueryClient();

  useEffect(() => {
    const source = new EventSource(`${API_PREFIX}/events/stream`);
    let timer: number | undefined;

    const invalidate = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        void client.invalidateQueries({ queryKey: runKeys.all });
      }, DEBOUNCE_MS);
    };

    source.onopen = () => setConnectionState("live");
    // the browser retries after an error unless it closed the stream for good
    source.onerror = () =>
      setConnectionState(source.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
    source.onmessage = (message: MessageEvent) => {
      const type = eventType(message.data);
      if (type !== null && LIST_EVENTS.includes(type)) invalidate();
    };

    return () => {
      window.clearTimeout(timer);
      source.close();
      setConnectionState("offline");
    };
  }, [client]);
}
