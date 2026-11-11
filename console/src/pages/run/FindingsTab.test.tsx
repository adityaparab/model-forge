import { expect, test } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../../App";
import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { RUN_ID, fixtures, installFetchStub } from "../../test/server";

type FindingsResponse = Schema<"FindingsResponse">;

test("findings tab groups by round and filters by status", async () => {
  const user = userEvent.setup();
  const findings = fixtures<FindingsResponse>("findings");
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/findings`, body: findings },
  ]);
  renderApp(<App />, { route: `/runs/${RUN_ID}/findings` });

  const summary = await screen.findByRole("region", { name: "Findings summary" });
  expect(within(summary).getByText(String(findings.summary.n_findings))).toBeInTheDocument();
  expect(within(summary).getByLabelText("Max open severity")).toHaveTextContent("low");
  expect(within(summary).getByText("disputed 1")).toBeInTheDocument();
  expect(within(summary).getByText("closed 4")).toBeInTheDocument();
  expect(within(summary).getByText("medium 4")).toBeInTheDocument();

  const rounds = [...new Set(findings.findings.map((f) => f.round))].sort((a, b) => a - b);
  const groups = screen.getAllByRole("region", { name: /^Round \d+$/ });
  expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(rounds.map((r) => `Round ${r}`));

  const round1 = screen.getByRole("region", { name: "Round 1" });
  const cards = within(round1).getAllByRole("article");
  expect(cards).toHaveLength(findings.findings.filter((f) => f.round === 1).length);

  const responses = within(round1).getByRole("list", { name: "Responses in round 1" });
  expect(within(responses).getAllByRole("listitem")).toHaveLength(
    findings.responses.filter((r) => r.round === 1).length,
  );
  const regenerated = within(round1).getByRole("list", { name: "Sections regenerated in round 1" });
  expect(within(regenerated).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "performance",
    "monitoring",
  ]);

  const first = within(round1).getByRole("article", { name: "F-1" });
  expect(within(first).getByText(findings.findings[0].title)).toBeInTheDocument();
  expect(within(first).getByText("precheck")).toBeInTheDocument();
  expect(within(first).getByRole("link", { name: "performance" })).toHaveAttribute(
    "href",
    `/runs/${RUN_ID}/document#performance`,
  );
  expect(within(first).getByRole("button", { name: /^Citation \[A:/ })).toBeInTheDocument();
  expect(within(first).getByText("check passes after remediation")).toBeInTheDocument();
  expect(within(first).getByText("closed in round 2")).toBeInTheDocument();

  await user.selectOptions(screen.getByLabelText("Status"), "disputed");
  const visible = screen.getAllByRole("article");
  const disputed = findings.findings.filter((f) => f.status === "disputed");
  expect(visible.map((card) => card.getAttribute("aria-label"))).toEqual(disputed.map((f) => f.id));
  const card = visible[0];
  expect(within(card).getByText(disputed[0].response ?? "")).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: /^Citation \[A:5c2859d2/ })).toBeInTheDocument();

  await user.selectOptions(screen.getByLabelText("Status"), "");
  await user.selectOptions(screen.getByLabelText("Severity"), "low");
  expect(screen.getAllByRole("article")).toHaveLength(
    findings.findings.filter((f) => f.severity === "low").length,
  );
});

test("findings filters narrow the round's responses but not its regenerated sections", async () => {
  const user = userEvent.setup();
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/findings`, body: fixtures("findings") },
  ]);
  renderApp(<App />, { route: `/runs/${RUN_ID}/findings` });

  await user.selectOptions(await screen.findByLabelText("Status"), "disputed");
  const round1 = screen.getByRole("region", { name: "Round 1" });
  const responses = within(round1).getByRole("list", { name: "Responses in round 1" });
  const items = within(responses).getAllByRole("listitem");
  expect(items).toHaveLength(1);
  expect(within(items[0]).getByText("F-5")).toBeInTheDocument();
  const regenerated = within(round1).getByRole("list", { name: "Sections regenerated in round 1" });
  expect(within(regenerated).getAllByRole("listitem")).toHaveLength(2);
});

test("an evidence chip shows the resolved value in its popover", async () => {
  const user = userEvent.setup();
  const findings = fixtures<FindingsResponse>("findings");
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/findings`, body: findings },
  ]);
  renderApp(<App />, { route: `/runs/${RUN_ID}/findings` });

  const [finding] = findings.findings;
  const resolved = finding.citations.find((c) => c.resolved && c.path?.startsWith("tests["));
  if (resolved === undefined) throw new Error("the fixture has no resolved test-status evidence");
  const card = await screen.findByRole("article", { name: finding.id });
  const chip = within(card).getByRole("button", { name: `Citation ${resolved.marker}` });
  expect(chip).toHaveAttribute("data-resolved", "true");
  await user.hover(chip);
  const popover = await screen.findByRole("dialog");
  expect(popover).not.toHaveTextContent("Unresolved citation");
  expect(popover).toHaveTextContent(JSON.stringify(resolved.value));
  expect(popover).toHaveTextContent(resolved.artifact_kind ?? "");
});
