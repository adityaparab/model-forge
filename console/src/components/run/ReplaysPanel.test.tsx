import { expect, test } from "vitest";
import { screen } from "@testing-library/react";

import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { fixtures } from "../../test/server";
import { ReplaysPanel } from "./ReplaysPanel";

type ReplayItem = Schema<"ReplayItem">;

/** The fixture's plain replay, reshaped: `matched` artifacts, mode and status as given. */
function replay(
  id: string,
  {
    matched,
    rerunLlm,
    status = "completed",
  }: { matched: number; rerunLlm: boolean; status?: string },
): ReplayItem {
  const [base] = fixtures<ReplayItem[]>("replays");
  if (base.report === null) throw new Error("the fixture replay has no report");
  const settled = status === "completed";
  return {
    run: {
      ...base.run,
      id,
      status: status as ReplayItem["run"]["status"],
      rerun_llm: rerunLlm,
    },
    report: settled
      ? {
          ...base.report,
          replay_run_id: id,
          matched: base.report.matched.slice(0, matched),
        }
      : null,
    report_missing: false,
  };
}

function verdict(): string {
  return (
    screen.getByRole("status", { name: "Reproducibility" }).textContent ?? ""
  );
}

test("a reproduced plain replay wins over a later --rerun-llm replay", () => {
  renderApp(
    <ReplaysPanel
      replays={[
        replay("run_plain", { matched: 23, rerunLlm: false }),
        replay("run_llm", { matched: 20, rerunLlm: true }),
      ]}
    />,
  );
  expect(verdict()).toBe("Reproduced: 23 artifacts matched");
});

test("without a reproduced plain replay an in-flight replay shows, then the latest other one", () => {
  const { unmount } = renderApp(
    <ReplaysPanel
      replays={[
        replay("run_running", {
          matched: 0,
          rerunLlm: false,
          status: "running",
        }),
        replay("run_llm", { matched: 20, rerunLlm: true }),
      ]}
    />,
  );
  expect(verdict()).toBe("Replay running");
  unmount();
  renderApp(
    <ReplaysPanel
      replays={[replay("run_llm", { matched: 20, rerunLlm: true })]}
    />,
  );
  expect(verdict()).toBe("Replay with --rerun-llm matched 20 artifacts");
});

test("a replay with a missing report shows report missing", () => {
  const sealed = replay("run_sealed", { matched: 23, rerunLlm: false });
  renderApp(
    <ReplaysPanel
      replays={[{ ...sealed, report: null, report_missing: true }]}
    />,
  );
  const [, row] = screen.getAllByRole("row");
  const status = row.querySelectorAll("td")[2];
  expect(status.textContent).toBe("report missing");
  expect(verdict()).not.toMatch(/^Reproduced/);
});
