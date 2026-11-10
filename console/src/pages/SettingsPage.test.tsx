import { expect, test } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";

import type { Schema } from "../api/client";
import { getIdentity } from "../identity";
import { SettingsPage } from "./SettingsPage";
import { renderApp } from "../test/render";
import { calls, fixtures, installFetchStub } from "../test/server";

type Settings = Schema<"SettingsResponse">;

function renderPage() {
  return renderApp(
    <Routes>
      <Route path="/settings" element={<SettingsPage />} />
    </Routes>,
    { route: "/settings" },
  );
}

test("settings page patches editable values and shows audit", async () => {
  const user = userEvent.setup();
  let current = fixtures<Settings>("settings");
  installFetchStub([
    { path: "/api/v1/settings", body: () => current },
    {
      method: "PATCH",
      path: "/api/v1/settings",
      body: () => {
        current = {
          ...current,
          editable: { ...current.editable, auto_replay: true },
          audit: [
            {
              id: 1,
              key: "auto_replay",
              old_value: false,
              new_value: true,
              updated_by: "alice",
              ts: "2026-11-11T09:00:00Z",
            },
          ],
        };
        return current;
      },
    },
  ]);
  renderPage();

  const autoReplay = await screen.findByRole("checkbox", { name: "Auto replay" });
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Default profile" })).toHaveValue("fake"),
  );
  expect(screen.getByRole("spinbutton", { name: "Default cost cap (USD)" })).toHaveValue(10);
  expect(autoReplay).not.toBeChecked();
  expect(screen.getByText("No settings changes yet.")).toBeInTheDocument();

  await user.click(autoReplay);
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.some((call) => call.method === "PATCH")).toBe(true));
  expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ auto_replay: true });

  const audit = await screen.findByRole("table", { name: "Settings audit" });
  const row = within(within(audit).getAllByRole("row")[1]);
  expect(row.getByText("auto_replay")).toBeInTheDocument();
  expect(row.getByText("false")).toBeInTheDocument();
  expect(row.getByText("true")).toBeInTheDocument();
  expect(row.getByText("alice")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Auto replay" })).toBeChecked();

  // a 403 from the role check shows the server's message
  calls.length = 0;
  await user.click(screen.getByRole("checkbox", { name: "Auto replay" }));
  installFetchStub([
    { path: "/api/v1/settings", body: current },
    {
      method: "PATCH",
      path: "/api/v1/settings",
      status: 403,
      body: { error: { kind: "role", message: "admin required" } },
    },
  ]);
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("admin required");
  expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ auto_replay: false });

  // the read-only configuration
  const profiles = screen.getByRole("table", { name: "Profile routes" });
  expect(within(profiles).getAllByText("fake").length).toBeGreaterThan(0);
  const prices = screen.getByRole("table", { name: "Profile prices" });
  expect(within(prices).getAllByText("claude-opus-5-5").length).toBeGreaterThan(0);
  expect(screen.getByText("http://localhost:16686")).toBeInTheDocument();
  expect(screen.getByText("http://localhost:6006")).toBeInTheDocument();
  expect(screen.queryByText(/prompts and completions leave the process/)).not.toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Role map" })).not.toBeInTheDocument();
});

test("settings page binds the identity, shows the role map and warns on content tracing", async () => {
  const user = userEvent.setup();
  const settings = fixtures<Settings>("settings");
  settings.config.identity = { mode: "roles", roles: { bob: ["admin", "validator"] } };
  settings.config.tracing = { endpoints: ["http://collector:4318"], content: true };
  installFetchStub([{ path: "/api/v1/settings", body: settings }]);
  renderPage();

  const identity = screen.getByRole("textbox", { name: "Acting identity" });
  await user.type(identity, "bob");
  expect(getIdentity()).toBe("bob");

  const roles = await screen.findByRole("table", { name: "Role map" });
  expect(within(roles).getByText("bob")).toBeInTheDocument();
  expect(within(roles).getByText("admin, validator")).toBeInTheDocument();
  expect(screen.getByText("http://collector:4318")).toBeInTheDocument();
  expect(screen.getByText(/prompts and completions leave the process/)).toBeInTheDocument();
});
