import { expect, test } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../../App";
import type { Schema } from "../../api/client";
import { renderApp } from "../../test/render";
import { RUN_ID, calls, fixtures, installFetchStub, type StubRoute } from "../../test/server";

type RunDetail = Schema<"RunDetail">;
type Gate = "scope" | "model" | "signoff";

const AWAITING = {
  scope: "awaiting_scope",
  model: "awaiting_model",
  signoff: "awaiting_signoff",
} as const;

/** The run fixture parked at `gate`, with the summary the fixture run's own gate showed. */
function awaiting(gate: Gate): RunDetail {
  const detail = fixtures<RunDetail>("run");
  const { events } = fixtures<Schema<"EventsResponse">>("events");
  const requested = events.find(
    (event) => event.type === "gate.requested" && event.payload.gate === gate,
  );
  if (requested === undefined) throw new Error(`no gate.requested for ${gate}`);
  detail.run.status = AWAITING[gate];
  detail.gate = { gate, summary: requested.payload.summary as Record<string, unknown> };
  return detail;
}

function stubRun(
  detail: RunDetail,
  approval: Omit<StubRoute, "path" | "method">,
  extra: StubRoute[] = [],
) {
  installFetchStub([
    ...extra,
    { path: `/api/v1/runs/${RUN_ID}`, body: detail },
    { path: `/api/v1/runs/${RUN_ID}/steps`, body: fixtures("steps") },
    { path: "/api/v1/config", body: fixtures("config") },
    { method: "POST", path: `/api/v1/runs/${RUN_ID}/approvals`, ...approval },
  ]);
}

function approvalCall() {
  return calls.find(
    (call) => call.method === "POST" && call.url === `/api/v1/runs/${RUN_ID}/approvals`,
  );
}

test("gate panel posts a sign-off approval and shows a refusal", async () => {
  const user = userEvent.setup();
  stubRun(awaiting("signoff"), {
    status: 409,
    body: {
      error: {
        kind: "approval",
        message: "refused",
        findings: [{ id: "F-1", severity: "high", title: "t" }],
      },
    },
  });
  renderApp(<App />, { route: `/runs/${RUN_ID}` });

  const panel = await screen.findByRole("region", { name: "Sign-off gate" });
  expect(within(panel).getByText("max open severity")).toBeInTheDocument();
  expect(within(panel).getByRole("radio", { name: "approve" })).toBeInTheDocument();
  expect(within(panel).getByRole("radio", { name: "reject" })).toBeInTheDocument();

  await user.click(within(panel).getByRole("radio", { name: "approve with conditions" }));
  await user.type(within(panel).getByRole("textbox", { name: "Conditions (one per line)" }), "c1");
  await user.click(within(panel).getByRole("button", { name: /^Record decision/ }));

  await waitFor(() => expect(approvalCall()).toBeDefined());
  expect(approvalCall()?.body).toEqual({
    gate: "signoff",
    decision: "approve_with_conditions",
    conditions: ["c1"],
  });

  const refusal = await within(panel).findByRole("alert");
  expect(refusal).toHaveTextContent("refused");
  expect(within(refusal).getByText("F-1")).toBeInTheDocument();
  expect(within(refusal).getByText("high")).toBeInTheDocument();
  expect(within(refusal).getByText("t")).toBeInTheDocument();
});

test("scope gate edit sends the edited plan", async () => {
  const user = userEvent.setup();
  const detail = awaiting("scope");
  stubRun(detail, { status: 202, body: detail.approvals[0] });
  renderApp(<App />, { route: `/runs/${RUN_ID}` });

  const panel = await screen.findByRole("region", { name: "Scope gate" });
  const editor = within(panel).getByRole("textbox", { name: "Plan (JSON)" });
  const plan = JSON.parse((editor as HTMLTextAreaElement).value) as Record<string, unknown>;
  expect(plan.model_name).toBe("synthetic_pd");

  await user.clear(editor);
  await user.click(editor);
  await user.paste("{ not json");
  expect(within(panel).getByText(/^Invalid JSON/)).toBeInTheDocument();

  await user.clear(editor);
  await user.click(editor);
  await user.paste(JSON.stringify({ ...plan, model_name: "edited_pd" }, null, 2));
  expect(within(panel).queryByText(/^Invalid JSON/)).not.toBeInTheDocument();
  expect(within(panel).getByText("model_name")).toBeInTheDocument();

  await user.click(within(panel).getByRole("radio", { name: "edit" }));
  await user.click(within(panel).getByRole("button", { name: /^Record decision/ }));

  await waitFor(() => expect(approvalCall()).toBeDefined());
  const body = approvalCall()?.body as Schema<"ApprovalRequest">;
  expect(body.gate).toBe("scope");
  expect(body.decision).toBe("edit");
  expect(body.plan?.model_name).toBe("edited_pd");
  expect(body.plan?.tier).toBe(plan.tier);

  expect(await within(panel).findByRole("status")).toHaveTextContent("edit recorded");
  expect(within(panel).getByRole("button", { name: /^Record decision/ })).toBeDisabled();
});

/** The model gate over the fixture run, its plan artifact served from the scope gate's plan. */
function stubModelGate() {
  const detail = awaiting("model");
  const plan = awaiting("scope").gate?.summary.plan as Schema<"ModelingPlan">;
  stubRun(detail, { status: 202, body: detail.approvals[1] }, [
    { path: `/api/v1/runs/${RUN_ID}/artifacts/${detail.run.plan_ref}`, body: plan },
  ]);
  return plan;
}

async function replacePlan(
  user: ReturnType<typeof userEvent.setup>,
  editor: HTMLElement,
  plan: Schema<"ModelingPlan">,
) {
  await user.clear(editor);
  await user.click(editor);
  await user.paste(JSON.stringify(plan, null, 2));
}

test("model gate adjust sends the target and the edited plan", async () => {
  const user = userEvent.setup();
  const plan = stubModelGate();
  renderApp(<App />, { route: `/runs/${RUN_ID}` });

  const panel = await screen.findByRole("region", { name: "Model gate" });
  expect(within(panel).getByRole("table", { name: "Validation tests" })).toBeInTheDocument();
  await user.click(within(panel).getByRole("radio", { name: "adjust" }));
  const editor = await within(panel).findByRole("textbox", { name: "Plan (JSON)" });
  await user.selectOptions(within(panel).getByRole("combobox", { name: "Go back to" }), "build");
  await replacePlan(user, editor, { ...plan, model_name: "adjusted_pd" });
  await user.click(within(panel).getByRole("button", { name: /^Record decision/ }));

  await waitFor(() => expect(approvalCall()).toBeDefined());
  expect(approvalCall()?.body).toEqual({
    gate: "model",
    decision: "adjust",
    adjust_target: "build",
    plan: { ...plan, model_name: "adjusted_pd" },
  });
});

test("model gate adjust posts only the plan the editor shows", async () => {
  const user = userEvent.setup();
  const plan = stubModelGate();
  renderApp(<App />, { route: `/runs/${RUN_ID}` });

  const panel = await screen.findByRole("region", { name: "Model gate" });
  await user.click(within(panel).getByRole("radio", { name: "adjust" }));
  let editor = await within(panel).findByRole("textbox", { name: "Plan (JSON)" });
  await replacePlan(user, editor, { ...plan, model_name: "hidden_pd" });

  await user.click(within(panel).getByRole("radio", { name: "accept" }));
  await user.click(within(panel).getByRole("radio", { name: "adjust" }));
  editor = await within(panel).findByRole("textbox", { name: "Plan (JSON)" });
  expect(JSON.parse((editor as HTMLTextAreaElement).value)).toEqual(plan);
  expect(within(panel).getByRole("button", { name: /^Record decision/ })).toBeDisabled();

  await replacePlan(user, editor, { ...plan, model_name: "shown_pd" });
  await user.click(within(panel).getByRole("button", { name: /^Record decision/ }));

  await waitFor(() => expect(approvalCall()).toBeDefined());
  const body = approvalCall()?.body as Schema<"ApprovalRequest">;
  expect(body.plan).toEqual(JSON.parse((editor as HTMLTextAreaElement).value));
  expect(body.plan?.model_name).toBe("shown_pd");
});
