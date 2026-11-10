import { expect, test } from "vitest";

import { splitMarkers } from "./markers";

test("splitMarkers finds nested-bracket markers", () => {
  const parts = splitMarkers("x [A:abc#tests[disc.auc.oot].status] y [K:k_1] [A:]");
  expect(parts).toEqual([
    { kind: "text", text: "x " },
    { kind: "marker", marker: "[A:abc#tests[disc.auc.oot].status]" },
    { kind: "text", text: " y " },
    { kind: "marker", marker: "[K:k_1]" },
    { kind: "text", text: " " },
    { kind: "marker", marker: "[A:]" },
  ]);
});

test("splitMarkers leaves text without markers whole and is stable across calls", () => {
  expect(splitMarkers("plain [link] text")).toEqual([{ kind: "text", text: "plain [link] text" }]);
  expect(splitMarkers("[K:k_2]")).toEqual([{ kind: "marker", marker: "[K:k_2]" }]);
  expect(splitMarkers("[K:k_2]")).toEqual([{ kind: "marker", marker: "[K:k_2]" }]);
  expect(splitMarkers("")).toEqual([]);
});
