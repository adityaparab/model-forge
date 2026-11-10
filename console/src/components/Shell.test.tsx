import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";

import { Shell } from "./Shell";
import { renderApp } from "../test/render";

test("shell shows navigation and persists the identity", async () => {
  const user = userEvent.setup();
  renderApp(
    <Routes>
      <Route element={<Shell />}>
        <Route path="/runs" element={<p>runs page</p>} />
      </Route>
    </Routes>,
    { route: "/runs" },
  );

  for (const name of ["Runs", "Inventory", "Costs", "Settings"]) {
    expect(screen.getByRole("link", { name })).toBeInTheDocument();
  }
  expect(screen.getByText("runs page")).toBeInTheDocument();
  expect(screen.getByRole("status", { name: "Connection" })).toHaveTextContent("offline");

  await user.type(screen.getByRole("textbox", { name: "Identity" }), "alice");
  expect(localStorage.getItem("modelforge.identity")).toBe("alice");
  expect(screen.getByRole("textbox", { name: "Identity" })).toHaveValue("alice");
});
