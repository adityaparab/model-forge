import { getIdentity } from "../identity";
import type { components } from "./types.gen";

export type paths = import("./types.gen").paths;
export type Schema<K extends keyof components["schemas"]> = components["schemas"][K];

export const API_PREFIX = "/api/v1";

/** An API error: the `{"error": {"kind", "message", ...}}` body, or the HTTP status. */
export class ApiError extends Error {
  kind: string;
  status: number;
  extra: Record<string, unknown>;

  constructor(kind: string, message: string, status: number, extra: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.extra = extra;
  }
}

export type ApiInit = RequestInit & { json?: unknown };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text === "") return undefined;
  const type = response.headers.get("Content-Type") ?? "";
  if (!type.includes("json")) return text;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function toError(status: number, statusText: string, body: unknown): ApiError {
  if (isRecord(body) && isRecord(body.error)) {
    const { kind, message, ...extra } = body.error;
    return new ApiError(
      typeof kind === "string" ? kind : "http",
      typeof message === "string" ? message : statusText,
      status,
      extra,
    );
  }
  return new ApiError("http", statusText || `HTTP ${status}`, status);
}

/** Calls `/api/v1{path}`; writes carry the identity header; error bodies raise `ApiError`. */
export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const { json, headers: given, ...rest } = init;
  const method = (rest.method ?? "GET").toUpperCase();
  const headers = new Headers(given);
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    rest.body = JSON.stringify(json);
  }
  const identity = getIdentity().trim();
  if (method !== "GET" && method !== "HEAD" && identity !== "") {
    headers.set("X-ModelForge-User", identity);
  }
  const response = await fetch(`${API_PREFIX}${path}`, { ...rest, method, headers });
  const body = await readBody(response);
  if (!response.ok) throw toError(response.status, response.statusText, body);
  return body as T;
}
