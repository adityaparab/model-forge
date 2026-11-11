import { expect, test } from "vitest";
import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router";

import type { Schema } from "../api/client";
import { InventoryPage } from "./InventoryPage";
import { renderApp } from "../test/render";
import { RUN_ID, fixtures, installFetchStub } from "../test/server";

test("inventory page lists entries", async () => {
  const inventory = fixtures<Schema<"InventoryEntry">[]>("inventory");
  inventory.push({
    ...inventory[0],
    model_id: "synthetic_pd@v2",
    version: 2,
    run_id: "run_conditioned",
    approver: "carol",
    conditions: ["recalibrate quarterly", "monitor PSI monthly"],
  });
  installFetchStub([{ path: "/api/v1/inventory", body: inventory }]);
  renderApp(
    <Routes>
      <Route path="/inventory" element={<InventoryPage />} />
    </Routes>,
    { route: "/inventory" },
  );

  const table = await screen.findByRole("table", { name: "Inventory" });
  const header = within(table).getAllByRole("columnheader").map((th) => th.textContent);
  expect(header).toEqual([
    "Model id",
    "Name",
    "Version",
    "Owner",
    "Tier",
    "Status",
    "Approver",
    "Conditions",
    "Run",
  ]);
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);

  const first = within(rows[0]);
  expect(first.getByText("synthetic_pd@v1")).toBeInTheDocument();
  expect(first.getByText("synthetic_pd")).toBeInTheDocument();
  expect(first.getByText("alice")).toBeInTheDocument();
  expect(first.getByText("approved")).toBeInTheDocument();
  expect(first.getByText("bob")).toBeInTheDocument();
  expect(first.getByRole("link", { name: RUN_ID })).toHaveAttribute(
    "href",
    `/runs/${RUN_ID}`,
  );

  const second = within(rows[1]);
  expect(second.getByText("carol")).toBeInTheDocument();
  expect(second.getByText("recalibrate quarterly")).toBeInTheDocument();
  expect(second.getByText("monitor PSI monthly")).toBeInTheDocument();
});

test("inventory page says when no model is approved yet", async () => {
  installFetchStub([{ path: "/api/v1/inventory", body: [] }]);
  renderApp(<InventoryPage />);
  expect(await screen.findByText(/No approved models yet/)).toBeInTheDocument();
});
