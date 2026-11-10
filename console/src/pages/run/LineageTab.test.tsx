import { expect, test } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../../App";
import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { RUN_ID, calls, fixtures, installFetchStub, type StubRequest } from "../../test/server";

test("lineage tab lists artifacts, events and verifies on demand", async () => {
  const user = userEvent.setup();
  const artifacts = fixtures<Schema<"ArtifactItem">[]>("artifacts");
  const events = fixtures<Schema<"EventsResponse">>("events");
  const manifest = fixtures<Schema<"Manifest">>("manifest");
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/artifacts`, body: artifacts },
    { path: /\/events\?/, body: events },
    { path: `/api/v1/runs/${RUN_ID}/manifest`, body: manifest },
    { path: `/api/v1/runs/${RUN_ID}/verify`, body: fixtures("verify") },
  ]);
  renderApp(<App />, { route: `/runs/${RUN_ID}/lineage` });

  const table = await screen.findByRole("table", { name: "Artifacts" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(artifacts.length);
  const first = artifacts[0];
  expect(within(rows[0]).getByText(first.kind)).toBeInTheDocument();
  expect(within(rows[0]).getByRole("button", { name: `Copy ${first.id}` })).toBeInTheDocument();
  expect(within(rows[0]).getByRole("link", { name: "view" })).toHaveAttribute(
    "href",
    `/api/v1/runs/${RUN_ID}/artifacts/${first.id}`,
  );

  const chain = await screen.findByRole("table", { name: "Event chain" });
  const eventRows = within(chain).getAllByRole("row").slice(1);
  expect(screen.getByText(`Showing ${events.events.length} events`)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  expect(eventRows).toHaveLength(events.events.length);
  expect(within(eventRows[0]).getByText(events.events[0].type)).toBeInTheDocument();
  expect(within(eventRows[0]).getByText(events.events[0].hash.slice(0, 12))).toBeInTheDocument();
  expect(within(eventRows[0]).getByText(events.events[0].prev_hash.slice(0, 12))).toBeInTheDocument();

  const sealed = await screen.findByRole("region", { name: "Manifest" });
  expect(within(sealed).getByText(String(Object.keys(manifest.refs).length))).toBeInTheDocument();
  expect(within(sealed).getByText("credit-modeling/fit_logistic_pd")).toBeInTheDocument();
  expect(within(sealed).getByText("completed")).toBeInTheDocument();

  expect(calls.some((call) => call.url.endsWith("/verify"))).toBe(false);
  await user.click(screen.getByRole("button", { name: "Verify chain" }));
  const report = await screen.findByRole("region", { name: "Verify report" });
  const verifyCalls = calls.filter((call) => call.url === `/api/v1/runs/${RUN_ID}/verify`);
  expect(verifyCalls).toHaveLength(1);
  expect(verifyCalls[0].method).toBe("GET");
  expect(within(report).getByLabelText("Verify result")).toHaveTextContent("ok");
  expect(within(report).getByText("170 events")).toBeInTheDocument();
  expect(within(report).getByText("71")).toBeInTheDocument();
});

test("lineage tab pages the event chain with load more", async () => {
  const user = userEvent.setup();
  const template = fixtures<Schema<"EventsResponse">>("events").events[0];
  const page = (from: number, n: number): Schema<"EventRecord">[] =>
    Array.from({ length: n }, (_, index) => ({
      ...template,
      id: from + index,
      type: `synthetic.${from + index}`,
      hash: `h${String(from + index).padStart(15, "0")}`,
    }));
  const firstPage = page(1, 500);
  const secondPage = page(501, 3);
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/artifacts`, body: [] },
    { path: `/api/v1/runs/${RUN_ID}/manifest`, body: fixtures("manifest") },
    {
      path: /\/events\?/,
      body: ({ url }: StubRequest) =>
        new URL(url, "http://x").searchParams.get("after") === "500"
          ? { events: secondPage, next_after: 503 }
          : { events: firstPage, next_after: 500 },
    },
  ]);
  renderApp(<App />, { route: `/runs/${RUN_ID}/lineage` });

  const chain = await screen.findByRole("table", { name: "Event chain" });
  expect(within(chain).getAllByRole("row").slice(1)).toHaveLength(500);
  expect(screen.getByText("Showing 500 events, more exist")).toBeInTheDocument();
  const first = calls.find((call) => call.url.includes("/events"));
  expect(first?.url).toBe(`/api/v1/runs/${RUN_ID}/events?limit=500`);

  await user.click(screen.getByRole("button", { name: "Load more" }));
  await screen.findByText("Showing 503 events");
  const eventCalls = calls.filter((call) => call.url.includes("/events"));
  expect(eventCalls.at(-1)?.url).toBe(`/api/v1/runs/${RUN_ID}/events?after=500&limit=500`);
  const rows = within(screen.getByRole("table", { name: "Event chain" })).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(503);
  expect(within(rows[502]).getByText("synthetic.503")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
});
