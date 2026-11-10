import { useSyncExternalStore } from "react";

/** The state of the server-sent event connection, shown in the top bar. */
export type ConnectionState = "live" | "reconnecting" | "offline";

let state: ConnectionState = "offline";
const listeners = new Set<() => void>();

export function getConnectionState(): ConnectionState {
  return state;
}

export function setConnectionState(next: ConnectionState): void {
  if (next === state) return;
  state = next;
  listeners.forEach((notify) => notify());
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify);
  return () => {
    listeners.delete(notify);
  };
}

export function useConnectionState(): ConnectionState {
  return useSyncExternalStore(subscribe, getConnectionState, getConnectionState);
}
