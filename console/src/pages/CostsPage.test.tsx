import { expect, test } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";

import type { Schema } from "../api/client";
import { CostsPage } from "./CostsPage";
import { renderApp } from "../test/render";
import { RUN_ID, calls, fixtures, installFetchStub } from "../test/server";

const DAY = 24 * 60 * 60 * 1000;

function sinceOf(url: string): number {
  const since = new URL(url, "http://localhost").searchParams.get("since");
  if (since === null) throw new Error(`no since in ${url}`);
  return Date.parse(since);
}

test("costs page renders tiles and bars for the window", async () => {
  const user = userEvent.setup();
  const costs = fixtures<Schema<"CostsResponse">>("costs");
  costs.by_day = [
    { day: "2026-11-09", cost_usd: 1.5, n_calls: 10 },
    { day: "2026-11-10", cost_usd: 3.0, n_calls: 35 },
  ];
  installFetchStub([{ path: "/api/v1/costs", body: costs }]);
  renderApp(
    <Routes>
      <Route path="/costs" element={<CostsPage />} />
    </Routes>,
    { route: "/costs" },
  );

  const tiles = await screen.findByRole("list", { name: "Totals" });
  expect(within(tiles).getByText("Total USD").nextSibling).toHaveTextContent("$0.00");
  expect(within(tiles).getByText("Runs").nextSibling).toHaveTextContent("1");
  expect(within(tiles).getByText("Calls").nextSibling).toHaveTextContent("35");
  expect(within(tiles).getByText("Tokens in").nextSibling).toHaveTextContent("74,564");
  expect(within(tiles).getByText("Tokens out").nextSibling).toHaveTextContent("3,424");

  // 30 days is the default window
  expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");
  const first = calls.find((call) => call.url.startsWith("/api/v1/costs"));
  expect(first).toBeDefined();
  expect(Date.now() - sinceOf(first?.url ?? "")).toBeGreaterThan(29 * DAY);

  const bars = screen.getByRole("list", { name: "Cost by day" });
  const half = within(bars).getByTitle("2026-11-09: $1.50");
  const full = within(bars).getByTitle("2026-11-10: $3.00");
  expect(half).toHaveStyle({ width: "50%" });
  expect(full).toHaveStyle({ width: "100%" });

  const byRun = screen.getByRole("table", { name: "Cost by run" });
  const runRows = within(byRun).getAllByRole("row").slice(1);
  expect(runRows).toHaveLength(2);
  const original = within(runRows[1]);
  expect(original.getByRole("link", { name: RUN_ID })).toHaveAttribute(
    "href",
    `/runs/${RUN_ID}`,
  );
  expect(original.getByText("completed")).toBeInTheDocument();
  expect(original.getByText("alice")).toBeInTheDocument();
  expect(original.getByText("$0.00 / $10.00")).toBeInTheDocument();
  expect(original.getByText("35")).toBeInTheDocument();

  expect(within(screen.getByRole("table", { name: "Cost by profile" })).getByText("fake")).toBeInTheDocument();
  expect(
    within(screen.getByRole("table", { name: "Cost by model" })).getByText("scripted-v1"),
  ).toBeInTheDocument();
  const byPurpose = screen.getByRole("table", { name: "Cost by purpose" });
  expect(within(byPurpose).getAllByRole("row").slice(1)).toHaveLength(4);
  expect(within(byPurpose).getByText("document")).toBeInTheDocument();

  calls.length = 0;
  await user.click(screen.getByRole("button", { name: "7 days" }));
  await waitFor(() =>
    expect(calls.some((call) => call.url.startsWith("/api/v1/costs?since="))).toBe(true),
  );
  const refetch = calls.find((call) => call.url.startsWith("/api/v1/costs"));
  const age = Date.now() - sinceOf(refetch?.url ?? "");
  expect(age).toBeGreaterThan(0);
  expect(age).toBeLessThan(8 * DAY);
  expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
});
