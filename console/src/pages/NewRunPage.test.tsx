import { expect, test } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";

import type { Schema } from "../api/client";
import { setIdentity } from "../identity";
import { NewRunPage } from "./NewRunPage";
import { renderApp } from "../test/render";
import { calls, fixtures, installFetchStub, RUN_ID, type StubRoute } from "../test/server";

const READS: StubRoute[] = [
  { path: "/api/v1/briefs", body: fixtures("briefs") },
  { path: "/api/v1/settings", body: fixtures("settings") },
];

function renderPage() {
  return renderApp(
    <Routes>
      <Route path="/runs/new" element={<NewRunPage />} />
      <Route path="/runs/:runId" element={<p>run page</p>} />
    </Routes>,
    { route: "/runs/new" },
  );
}

test("new run form fills from an example and submits with the identity header", async () => {
  const user = userEvent.setup();
  setIdentity("alice");
  const run = { ...fixtures<Schema<"RunListResponse">>("runs").runs[1], id: RUN_ID };
  installFetchStub([...READS, { method: "POST", path: "/api/v1/runs", body: run, status: 201 }]);
  renderPage();

  const profile = screen.getByRole("combobox", { name: "Profile" });
  await waitFor(() => expect(profile).toHaveValue("fake"));
  expect(screen.getByRole("spinbutton", { name: "Cost cap (USD)" })).toHaveValue(10);

  const example = screen.getByRole("combobox", { name: "Example" });
  await screen.findByRole("option", { name: "synthetic" });
  await user.selectOptions(example, "synthetic");
  const brief = screen.getByRole("textbox", { name: "Brief (YAML)" });
  expect((brief as HTMLTextAreaElement).value).toContain("model_name");

  await user.click(screen.getByRole("button", { name: "Submit run" }));
  expect(await screen.findByText("run page")).toBeInTheDocument();

  const post = calls.find((call) => call.method === "POST");
  expect(post?.url).toBe("/api/v1/runs");
  expect(post?.headers.get("X-ModelForge-User")).toBe("alice");
  expect(post?.body).toMatchObject({ profile: "fake", cost_cap_usd: 10 });
  expect((post?.body as { brief: string }).brief).toContain("model_name: synthetic_pd");
});

test("new run shows the server's brief error", async () => {
  const user = userEvent.setup();
  installFetchStub([
    ...READS,
    {
      method: "POST",
      path: "/api/v1/runs",
      body: { error: { kind: "brief", message: "unknown dataset adapter" } },
      status: 400,
    },
  ]);
  renderPage();

  await user.type(screen.getByRole("textbox", { name: "Brief (YAML)" }), "model_name: x");
  await user.click(screen.getByRole("button", { name: "Submit run" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("unknown dataset adapter");
  expect(screen.queryByText("run page")).not.toBeInTheDocument();
});
