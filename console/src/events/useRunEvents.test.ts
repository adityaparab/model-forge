import { expect, test, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactNode } from "react";

import type { Schema } from "../api/client";
import { getConnectionState } from "./connection";
import { useRunEvents } from "./useRunEvents";
import { EventSourceStub } from "../test/setup";
import { RUN_ID, fixtures } from "../test/server";

test("useRunEvents invalidates the run's queries and keeps events", async () => {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  const { result, unmount } = renderHook(() => useRunEvents(RUN_ID), { wrapper });

  expect(EventSourceStub.instances).toHaveLength(1);
  const source = EventSourceStub.instances[0];
  expect(source.url).toBe(`/api/v1/runs/${RUN_ID}/events/stream`);

  act(() => source.open());
  expect(getConnectionState()).toBe("live");
  expect(result.current.state).toBe("live");

  const { events } = fixtures<Schema<"EventsResponse">>("events");
  const [first, second] = events.filter((event) => event.type.startsWith("step."));
  act(() => {
    source.emit({ type: first.type, id: String(first.id), data: first });
    source.emit({ type: second.type, id: String(second.id), data: second });
  });

  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["runs", RUN_ID] }));
  expect(invalidate).toHaveBeenCalledTimes(1);
  expect(result.current.events).toHaveLength(2);
  expect(result.current.events.map((event) => event.id)).toEqual([first.id, second.id]);

  act(() => source.fail());
  expect(result.current.state).toBe("reconnecting");

  unmount();
  expect(source.readyState).toBe(EventSourceStub.CLOSED);
});
