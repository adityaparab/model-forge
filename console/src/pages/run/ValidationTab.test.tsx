import { expect, test } from "vitest";
import { screen, within } from "@testing-library/react";

import { App } from "../../App";
import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { RUN_ID, fixtures, installFetchStub } from "../../test/server";

type ValidationReport = Schema<"ValidationReport">;

test("validation tab lists thirteen tests", async () => {
  const report = fixtures<ValidationReport>("validation");
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/validation`, body: report },
  ]);
  renderApp(<App />, { route: `/runs/${RUN_ID}/validation` });

  const table = await screen.findByRole("table", { name: "Validation tests" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(13);
  const first = rows[0];
  expect(within(first).getByText("disc.auc.oot")).toBeInTheDocument();
  expect(within(first).getByText("Out-of-time AUC")).toBeInTheDocument();
  expect(within(first).getByText("pass")).toBeInTheDocument();
  expect(within(first).getByText("0.7373")).toBeInTheDocument();
  expect(within(first).getByText(">= 0.70 pass, >= 0.65 warn")).toBeInTheDocument();

  expect(screen.getByRole("status", { name: "Overall" })).toHaveTextContent("pass");

  const thresholds = screen.getByRole("region", { name: "Thresholds" });
  expect(within(thresholds).getByText("auc_min")).toBeInTheDocument();
  expect(within(thresholds).getAllByRole("term")).toHaveLength(
    Object.keys(report.thresholds).length,
  );
});
