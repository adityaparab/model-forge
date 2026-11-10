import { expect, test } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../../App";
import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { RUN_ID, calls, fixtures, installFetchStub } from "../../test/server";

test("lineage tab lists artifacts, events and verifies on demand", async () => {
  const user = userEvent.setup();
  const artifacts = fixtures<Schema<"ArtifactItem">[]>("artifacts");
  const events = fixtures<Schema<"EventsResponse">>("events");
  const manifest = fixtures<Schema<"Manifest">>("manifest");
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/artifacts`, body: artifacts },
    { path: `/api/v1/runs/${RUN_ID}/events`, body: events },
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
