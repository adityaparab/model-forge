import { expect, test } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";

import type { Schema } from "../api/client";
import { RunsPage } from "./RunsPage";
import { renderApp } from "../test/render";
import { calls, fixtures, installFetchStub } from "../test/server";

test("runs page lists fixture runs with status pills and filters by status", async () => {
  const user = userEvent.setup();
  const list = fixtures<Schema<"RunListResponse">>("runs");
  installFetchStub([{ path: "/api/v1/runs", body: list }]);
  renderApp(
    <Routes>
      <Route path="/runs" element={<RunsPage />} />
    </Routes>,
    { route: "/runs" },
  );

  const table = await screen.findByRole("table", { name: "Runs" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(list.runs.length);

  const [replay, original] = list.runs;
  const first = within(rows[0]);
  const link = first.getByRole("link", { name: replay.id });
  expect(link).toHaveAttribute("href", `/runs/${replay.id}`);
  expect(first.getByText("completed")).toBeInTheDocument();
  expect(first.getByTitle(`replay of ${replay.replay_of}`)).toHaveTextContent("replay");
  expect(first.getByText("0.00 / 10.00")).toBeInTheDocument();
  expect(first.getByText("low")).toBeInTheDocument();
  expect(within(rows[1]).getByText(original.model_name ?? "")).toBeInTheDocument();
  expect(within(rows[1]).queryByTitle(/^replay of/)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "New run" })).toHaveAttribute("href", "/runs/new");

  await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "completed");
  await waitFor(() =>
    expect(calls.some((call) => call.url.includes("status=completed"))).toBe(true),
  );

  await user.click(screen.getByRole("checkbox", { name: "Hide replays" }));
  await waitFor(() =>
    expect(calls.some((call) => call.url.includes("replays=exclude"))).toBe(true),
  );
});
