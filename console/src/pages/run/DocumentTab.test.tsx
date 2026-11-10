import { expect, test } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../../App";
import { Markdown } from "../../components/doc/Markdown";
import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { RUN_ID, fixtures, installFetchStub } from "../../test/server";

type DocumentResponse = Schema<"DocumentResponse">;

function stubDocument(document: DocumentResponse) {
  installFetchStub([
    { path: `/api/v1/runs/${RUN_ID}`, body: fixtures("run") },
    { path: `/api/v1/runs/${RUN_ID}/document`, body: document },
  ]);
}

test("document tab renders eleven sections with grounding badges and citation popovers", async () => {
  const user = userEvent.setup();
  const document = fixtures<DocumentResponse>("document");
  stubDocument(document);
  renderApp(<App />, { route: `/runs/${RUN_ID}/document` });

  const body = await screen.findByRole("region", { name: "Document" });
  const headings = within(body).getAllByRole("heading", { level: 2 });
  expect(headings.map((h) => h.textContent)).toEqual(document.sections.map((s) => s.title));
  expect(within(body).getAllByText("grounded")).toHaveLength(11);

  const nav = screen.getByRole("navigation", { name: "Sections" });
  const links = within(nav).getAllByRole("link");
  expect(links).toHaveLength(11);
  expect(links[0]).toHaveAttribute("href", "#executive_summary");

  expect(screen.getByRole("link", { name: "Download HTML" })).toHaveAttribute(
    "href",
    `/api/v1/runs/${RUN_ID}/document?format=html`,
  );
  expect(screen.getByRole("link", { name: "Download Markdown" })).toHaveAttribute(
    "href",
    `/api/v1/runs/${RUN_ID}/document?format=md`,
  );

  // An A chip: hover shows the artifact kind, path and value as JSON.
  const performance = within(body).getByRole("region", { name: "Performance and validation results" });
  const aucMarker = document.sections
    .find((s) => s.section_id === "performance")
    ?.citations.find((c) => c.path === "by_split.val.auc")?.marker;
  const aChip = within(performance).getAllByRole("button", { name: `Citation ${aucMarker}` })[0];
  expect(aChip).toHaveAttribute("data-kind", "A");
  await user.hover(aChip);
  const aPopover = await screen.findByRole("dialog");
  expect(within(aPopover).getByText("discrimination")).toBeInTheDocument();
  expect(within(aPopover).getByText("by_split.val.auc")).toBeInTheDocument();
  expect(within(aPopover).getByText("0.7391014492753624")).toBeInTheDocument();
  await user.unhover(aChip);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  // A K chip: keyboard focus shows the passage title, section and text.
  const regulatory = within(body).getByRole("region", { name: "Regulatory and policy context" });
  const kChip = within(regulatory).getAllByRole("button", {
    name: "Citation [K:k_b1860ab50e4c]",
  })[0];
  expect(kChip).toHaveAttribute("data-kind", "K");
  kChip.focus();
  const kPopover = await screen.findByRole("dialog");
  expect(
    within(kPopover).getByText("SR 26-2: Revised Guidance on Model Risk Management"),
  ).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("flagged sections, failing grounding and unresolved or truncated citations are marked", async () => {
  const user = userEvent.setup();
  const document = fixtures<DocumentResponse>("document");
  const [summary, purpose] = document.sections;
  summary.body_md =
    "> **REQUIRES AUTHOR ATTENTION**: this section was not supplied.\n\nSee [A:dead#x] and [A:beef#rows].";
  summary.grounding = {
    ok: false,
    ungrounded_numbers: ["0.42", "17"],
    unresolved_markers: ["[A:dead#x]"],
    uncited_regulatory_sentences: [],
    foreign_passages: [],
  };
  summary.citations = [
    {
      marker: "[A:dead#x]",
      kind: "A",
      target: "dead#x",
      resolved: false,
      note: "artifact not in this run",
    },
    {
      marker: "[A:beef#rows]",
      kind: "A",
      target: "beef#rows",
      resolved: true,
      artifact_id: "beef",
      artifact_kind: "profile",
      path: "rows",
      value: [1, 2, 3],
      note: "truncated",
    },
  ];
  purpose.grounding = null;
  stubDocument(document);
  renderApp(<App />, { route: `/runs/${RUN_ID}/document` });

  const nav = await screen.findByRole("navigation", { name: "Sections" });
  const flagged = within(nav).getAllByText("needs attention");
  expect(flagged).toHaveLength(1);
  expect(within(nav).getByRole("link", { name: /Executive summary/ })).toHaveTextContent(
    "needs attention",
  );

  const section = screen.getByRole("region", { name: "Executive summary" });
  const badge = within(section).getByRole("status", { name: "Grounding" });
  expect(badge).toHaveTextContent("Ungrounded numbers 2");
  expect(badge).toHaveTextContent("Unresolved markers 1");
  expect(badge).toHaveTextContent("Uncited regulatory sentences 0");
  expect(badge).toHaveTextContent("Foreign passages 0");
  expect(within(section).getByText(/REQUIRES AUTHOR ATTENTION/)).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "Model purpose and intended use" })).getByText(
      "not checked",
    ),
  ).toBeInTheDocument();

  const unresolved = within(section).getByRole("button", { name: "Citation [A:dead#x]" });
  expect(unresolved).toHaveAttribute("data-resolved", "false");
  await user.hover(unresolved);
  expect(await screen.findByRole("dialog")).toHaveTextContent("artifact not in this run");
  await user.unhover(unresolved);

  const truncated = within(section).getByRole("button", { name: "Citation [A:beef#rows]" });
  await user.hover(truncated);
  const popover = await screen.findByRole("dialog");
  expect(popover).toHaveTextContent("truncated");
  expect(popover).toHaveTextContent(/\[\s*1,\s*2,\s*3\s*\]/);
});

test("markdown turns nested-bracket markers into chips and never renders raw HTML", () => {
  const marker = "[A:abc#tests[disc.auc.oot].value]";
  const { container } = render(
    <Markdown
      body={`AUC is 0.74 ${marker}, see \`[K:k_1]\`.\n\n<script>alert(1)</script><b>bold</b>`}
      citations={[]}
    />,
  );
  const chip = screen.getByRole("button", { name: `Citation ${marker}` });
  expect(chip).toHaveAttribute("data-resolved", "false");
  expect(chip).toHaveTextContent("A:abc#tests[disc.auc.oot].value");
  expect(screen.queryByRole("button", { name: "Citation [K:k_1]" })).not.toBeInTheDocument();
  expect(screen.getByText("[K:k_1]").tagName).toBe("CODE");
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("b")).toBeNull();
});
