import { useSyncExternalStore } from "react";

/** The acting identity sent as `X-ModelForge-User` on writes, kept in localStorage. */
const KEY = "modelforge.identity";

const listeners = new Set<() => void>();

export function getIdentity(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setIdentity(name: string): void {
  try {
    localStorage.setItem(KEY, name);
  } catch {
    // storage unavailable (private mode): the identity lasts only as long as the input
  }
  listeners.forEach((notify) => notify());
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify);
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEY) notify();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(notify);
    window.removeEventListener("storage", onStorage);
  };
}

export function useIdentity(): [string, (name: string) => void] {
  const identity = useSyncExternalStore(subscribe, getIdentity, () => "");
  return [identity, setIdentity];
}
