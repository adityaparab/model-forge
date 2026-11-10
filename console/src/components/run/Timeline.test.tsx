import { expect, test } from "vitest";
import { render, screen, within } from "@testing-library/react";

import type { Schema } from "../../api/client";
import { fixtures } from "../../test/server";
import { Timeline } from "./Timeline";

type StepDetail = Schema<"StepDetail">;

function rowOf(node: string): HTMLElement {
  const table = screen.getByRole("table", { name: "Timeline" });
  const row = within(table)
    .getAllByRole("row")
    .find((candidate) => within(candidate).queryByRole("rowheader", { name: node }) !== null);
  if (row === undefined) throw new Error(`no row for ${node}`);
  return row;
}

test("timeline shows every node in graph order with its latest attempt", () => {
  const { nodes } = fixtures<Schema<"ConfigResponse">>("config");
  const steps = fixtures<StepDetail[]>("steps");
  render(<Timeline steps={steps} nodes={nodes} />);

  const table = screen.getByRole("table", { name: "Timeline" });
  const names = within(table)
    .getAllByRole("rowheader")
    .map((cell) => cell.textContent);
  expect(names).toEqual(nodes);

  const challenge = rowOf("challenge");
  const latest = steps.filter((step) => step.node === "challenge").at(-1);
  expect(within(challenge).getByText("succeeded")).toBeInTheDocument();
  expect(within(challenge).getByRole("cell", { name: "2" })).toBeInTheDocument();
  expect(within(challenge).getByText(`${latest?.duration_ms} ms`)).toBeInTheDocument();

  const build = rowOf("build");
  const buildStep = steps.find((step) => step.node === "build");
  expect(
    within(build).getByRole("cell", { name: String(buildStep?.tool_calls.length) }),
  ).toBeInTheDocument();
});

test("timeline marks running, failed and not started nodes", () => {
  const { nodes } = fixtures<Schema<"ConfigResponse">>("config");
  const steps = fixtures<StepDetail[]>("steps").slice(0, 2);
  steps[1] = { ...steps[1], status: "failed" };
  steps.push({ ...steps[1], id: "step_retry", attempt: 2, status: "running", finished_at: null });
  render(<Timeline steps={steps} nodes={nodes} />);

  expect(within(rowOf("intake")).getByText("succeeded")).toBeInTheDocument();
  expect(within(rowOf("scope")).getByText("running")).toBeInTheDocument();
  expect(within(rowOf("seal")).getByText("not started")).toBeInTheDocument();
});
