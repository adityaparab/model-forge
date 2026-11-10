import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { setConnectionState } from "../events/connection";
import { restoreFetch } from "./server";

type Listener = (event: MessageEvent) => void;

/** A controllable `EventSource`: tests open it, emit events into it and fail it. */
export class EventSourceStub {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: EventSourceStub[] = [];

  readonly url: string;
  readyState: number = EventSourceStub.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: Listener | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string | URL) {
    this.url = String(url);
    EventSourceStub.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    const set = this.listeners.get(type) ?? new Set<Listener>();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: Listener): void {
    this.listeners.get(type)?.delete(listener);
  }

  /** Marks the stream open and fires `open`. */
  open(): void {
    this.readyState = EventSourceStub.OPEN;
    this.onopen?.(new Event("open"));
  }

  /** Delivers one server-sent event; `data` is JSON-encoded unless it is already a string. */
  emit(event: { type?: string; data: unknown; id?: string }): void {
    const type = event.type ?? "message";
    const message = new MessageEvent(type, {
      data: typeof event.data === "string" ? event.data : JSON.stringify(event.data),
      lastEventId: event.id ?? "",
    });
    if (type === "message") this.onmessage?.(message);
    this.listeners.get(type)?.forEach((listener) => listener(message));
  }

  /** Fires `error`, as the browser does before reconnecting (`CONNECTING`) or after closing. */
  fail(readyState: number = EventSourceStub.CONNECTING): void {
    this.readyState = readyState;
    this.onerror?.(new Event("error"));
  }

  close(): void {
    this.readyState = EventSourceStub.CLOSED;
  }
}

globalThis.EventSource = EventSourceStub as unknown as typeof EventSource;

afterEach(() => {
  cleanup();
  restoreFetch();
  localStorage.clear();
  setConnectionState("offline");
  EventSourceStub.instances = [];
});
