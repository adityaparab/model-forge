import { expect, test } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../App";
import type { Schema } from "../api/client";
import { RunHeader } from "../components/run/RunHeader";
import { ReplaysPanel } from "../components/run/ReplaysPanel";
import { renderApp } from "../test/render";
import { RUN_ID, calls, fixtures, installFetchStub } from "../test/server";

type RunDetail = Schema<"RunDetail">;
type RunStatus = Schema<"RunRecord">["status"];

function stubRun(detail: RunDetail) {
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: detail },
    { path: `/api/v1/runs/${RUN_ID}/steps`, body: fixtures("steps") },
    { path: "/api/v1/config", body: fixtures("config") },
    { method: "POST", path: `/api/v1/runs/${RUN_ID}/pause`, body: detail.run, status: 202 },
  ]);
}

function withStatus(status: RunStatus): RunDetail {
  const detail = fixtures<RunDetail>("run");
  detail.run.status = status;
  return detail;
}

test("run page renders header, timeline with fourteen nodes and tabs", async () => {
  const detail = fixtures<RunDetail>("run");
  stubRun(detail);
  renderApp(<App />, { route: `/runs/${RUN_ID}` });

  expect(await screen.findByRole("heading", { name: RUN_ID })).toBeInTheDocument();
  const header = screen.getByRole("region", { name: "Run" });
  expect(within(header).getByText("completed")).toBeInTheDocument();
  expect(within(header).getByText(detail.inventory?.name ?? "")).toBeInTheDocument();
  expect(within(header).getByText("0.00 / 10.00")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Jaeger" }).getAttribute("href")).toMatch(
    new RegExp(`${detail.run.trace_id}$`),
  );
  expect(screen.getByRole("link", { name: "Jaeger" })).toHaveAttribute("target", "_blank");
  expect(screen.getByRole("link", { name: "Phoenix" })).toBeInTheDocument();

  const timeline = await screen.findByRole("table", { name: "Timeline" });
  const rows = within(timeline).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(14);
  const seal = rows.find((row) => within(row).queryByText("seal") !== null);
  expect(seal).toBeDefined();
  expect(within(seal as HTMLElement).getByText("succeeded")).toBeInTheDocument();

  const tabs = screen.getByRole("navigation", { name: "Run tabs" });
  for (const [name, path] of [
    ["Overview", ""],
    ["Document", "/document"],
    ["Validation", "/validation"],
    ["Findings", "/findings"],
    ["Lineage", "/lineage"],
    ["LLM calls", "/llm-calls"],
  ]) {
    expect(within(tabs).getByRole("link", { name })).toHaveAttribute(
      "href",
      `/runs/${RUN_ID}${path}`,
    );
  }
  expect(screen.getByRole("table", { name: "Approvals" })).toBeInTheDocument();
});

test("header actions follow the status", async () => {
  const user = userEvent.setup();
  stubRun(withStatus("running"));

  const running = renderApp(<RunHeader detail={withStatus("running")} />);
  expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Abort" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Pause" }));
  await waitFor(() =>
    expect(
      calls.some(
        (call) => call.method === "POST" && call.url === `/api/v1/runs/${RUN_ID}/pause`,
      ),
    ).toBe(true),
  );
  running.unmount();

  const requested = withStatus("running");
  requested.run.pause_requested = true;
  const pausing = renderApp(<RunHeader detail={requested} />);
  expect(screen.getByRole("button", { name: "Pause requested" })).toBeDisabled();
  pausing.unmount();

  const paused = renderApp(<RunHeader detail={withStatus("paused")} />);
  expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name: "New cost cap (USD)" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Abort" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
  paused.unmount();

  const awaiting = renderApp(<RunHeader detail={withStatus("awaiting_scope")} />);
  expect(screen.getByRole("button", { name: "Abort" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  awaiting.unmount();

  renderApp(<RunHeader detail={withStatus("completed")} />);
  expect(screen.getByRole("button", { name: "Replay" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Replay with --rerun-llm" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Abort" })).not.toBeInTheDocument();
});

test("replays panel shows the reproducibility badge", () => {
  const replays = fixtures<Schema<"ReplayItem">[]>("replays");
  const matched = replays[0].report?.matched.length ?? 0;
  const done = renderApp(<ReplaysPanel replays={replays} />);
  expect(screen.getByRole("status", { name: "Reproducibility" })).toHaveTextContent(
    `Reproduced: ${matched} artifacts matched`,
  );
  expect(screen.getByRole("link", { name: replays[0].run.id })).toBeInTheDocument();
  done.unmount();

  const mismatch = fixtures<Schema<"ReplayItem">[]>("replays");
  const report = mismatch[0].report;
  if (report === null) throw new Error("fixture replay has no report");
  report.ok = false;
  report.mismatched = [{ ref: "champion", original_id: "a", replay_id: "b" }];
  const bad = renderApp(<ReplaysPanel replays={mismatch} />);
  expect(screen.getByRole("status", { name: "Reproducibility" })).toHaveTextContent(
    "Mismatch: champion",
  );
  bad.unmount();

  const running = fixtures<Schema<"ReplayItem">[]>("replays");
  running[0].run.status = "running";
  running[0].report = null;
  renderApp(<ReplaysPanel replays={running} />);
  expect(screen.getByRole("status", { name: "Reproducibility" })).toHaveTextContent(
    "Replay running",
  );
});
