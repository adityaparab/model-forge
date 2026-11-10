import { afterEach, expect, test } from "vitest";

import { api, ApiError } from "./client";
import { setIdentity } from "../identity";
import { calls, installFetchStub, restoreFetch } from "../test/server";

afterEach(() => {
  restoreFetch();
});

test("api adds the identity header on writes and raises ApiError on error bodies", async () => {
  setIdentity("bob");
  installFetchStub([
    { method: "POST", path: "/api/v1/runs", body: { run_id: "r1" }, status: 201 },
    {
      method: "POST",
      path: /\/api\/v1\/runs\/r1\/approvals$/,
      body: { error: { kind: "approval", message: "m", gate: "scope" } },
      status: 409,
    },
    { method: "GET", path: "/api/v1/config", body: { console: "built" } },
  ]);

  await expect(api("/runs", { method: "POST", json: { brief: "x" } })).resolves.toEqual({
    run_id: "r1",
  });
  expect(calls[0].url).toBe("/api/v1/runs");
  expect(calls[0].headers.get("X-ModelForge-User")).toBe("bob");
  expect(calls[0].headers.get("Content-Type")).toBe("application/json");
  expect(calls[0].body).toEqual({ brief: "x" });

  const failure = await api("/runs/r1/approvals", { method: "POST", json: {} }).catch(
    (e: unknown) => e,
  );
  expect(failure).toBeInstanceOf(ApiError);
  const error = failure as ApiError;
  expect(error.kind).toBe("approval");
  expect(error.message).toBe("m");
  expect(error.status).toBe(409);
  expect(error.extra).toEqual({ gate: "scope" });

  await expect(api("/config")).resolves.toEqual({ console: "built" });
  expect(calls[2].method).toBe("GET");
  expect(calls[2].headers.has("X-ModelForge-User")).toBe(false);
});

test("api raises a network ApiError when fetch itself rejects", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };

  const failure = await api("/config").catch((e: unknown) => e);
  expect(failure).toBeInstanceOf(ApiError);
  const error = failure as ApiError;
  expect(error.kind).toBe("network");
  expect(error.message).toBe("Failed to fetch");
  expect(error.status).toBe(0);
});
