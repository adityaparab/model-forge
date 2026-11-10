import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { API_PREFIX } from "../api/client";
import { runKeys } from "../api/queries";
import { setConnectionState } from "./connection";

/** The event types that change what the run list shows. */
const LIST_EVENTS = ["run.created", "run.status_changed", "gate.requested", "run.sealed"] as const;

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
    LIST_EVENTS.forEach((type) => source.addEventListener(type, invalidate));

    return () => {
      window.clearTimeout(timer);
      LIST_EVENTS.forEach((type) => source.removeEventListener(type, invalidate));
      source.close();
      setConnectionState("offline");
    };
  }, [client]);
}
