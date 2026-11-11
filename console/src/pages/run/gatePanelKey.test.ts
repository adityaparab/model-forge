import { expect, test } from "vitest";

import type { Schema } from "../../api/client";
import { fixtures } from "../../test/server";
import { gatePanelKey } from "./gatePanelKey";

type RunDetail = Schema<"RunDetail">;

test("the gate panel key changes when an approval is added at the same gate", () => {
  const detail = fixtures<RunDetail>("run");
  const gate = { gate: "model" as const, summary: {} };
  const before = gatePanelKey({ ...detail, gate });
  // an adjust at the model gate, and the run is back at the model gate
  const after = gatePanelKey({
    ...detail,
    gate,
    approvals: [...detail.approvals, detail.approvals[0]],
  });
  expect(after).not.toBe(before);
  expect(gatePanelKey({ ...detail, gate })).toBe(before);
});
