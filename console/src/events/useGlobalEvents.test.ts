import { expect, test, vi } from "vitest";
import { act, waitFor } from "@testing-library/react";
import { createElement } from "react";

import { getConnectionState } from "./connection";
import { useGlobalEvents } from "./useGlobalEvents";
import { renderApp } from "../test/render";
import { EventSourceStub } from "../test/setup";

function Probe() {
  useGlobalEvents();
  return null;
}

test("global events invalidate the run list and track the connection", async () => {
  const { queryClient, unmount } = renderApp(createElement(Probe));
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");

  expect(EventSourceStub.instances).toHaveLength(1);
  const source = EventSourceStub.instances[0];
  expect(source.url).toBe("/api/v1/events/stream");

  act(() => source.open());
  expect(getConnectionState()).toBe("live");

  // unnamed frames, as the server sends; a type the run list does not show is ignored
  act(() => source.emit({ id: "2", data: { id: 2, type: "step.started" } }));
  await act(() => new Promise((resolve) => setTimeout(resolve, 400)));
  expect(invalidate).not.toHaveBeenCalled();

  act(() => {
    source.emit({ id: "3", data: { id: 3, type: "run.created" } });
    source.emit({ id: "4", data: { id: 4, type: "run.status_changed" } });
  });
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["runs"] }));
  expect(invalidate).toHaveBeenCalledTimes(1);

  act(() => source.fail());
  expect(getConnectionState()).toBe("reconnecting");

  unmount();
  expect(source.readyState).toBe(EventSourceStub.CLOSED);
});
