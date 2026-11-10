import { expect, test } from "vitest";
import { screen, within } from "@testing-library/react";

import { App } from "../../App";
import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { RUN_ID, fixtures, installFetchStub } from "../../test/server";

test("llm calls tab totals by purpose", async () => {
  const llm = fixtures<Schema<"LlmCallsResponse">>("llm-calls");
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/llm-calls`, body: llm },
  ]);
  renderApp(<App />, { route: `/runs/${RUN_ID}/llm-calls` });

  const spend = await screen.findByLabelText("Spend");
  expect(spend).toHaveTextContent("$0.00 / $10.00");

  const byPurpose = screen.getByRole("table", { name: "Cost by purpose" });
  const purposeRows = within(byPurpose).getAllByRole("row").slice(1);
  expect(purposeRows).toHaveLength(4);
  expect(purposeRows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual(
    Object.keys(llm.cost.by_purpose),
  );
  expect(within(purposeRows[1]).getByText("22")).toBeInTheDocument();

  const byModel = screen.getByRole("table", { name: "Cost by model" });
  expect(within(byModel).getAllByRole("row").slice(1)).toHaveLength(
    Object.keys(llm.cost.by_model).length,
  );

  const table = screen.getByRole("table", { name: "LLM calls" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(llm.calls.length);
  const first = llm.calls[0];
  expect(within(rows[0]).getByText(`${first.prompt_name} v${first.prompt_version}`)).toBeInTheDocument();
  expect(within(rows[0]).getByText(first.model)).toBeInTheDocument();
  expect(within(rows[0]).getByText("1,875")).toBeInTheDocument();
  expect(within(rows[0]).getByText("end_turn")).toBeInTheDocument();
});
