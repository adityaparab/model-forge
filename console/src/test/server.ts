import runId from "../fixtures/run-id.txt?raw";

/** The captured fixtures (`make console-fixtures`), keyed by file name without `.json`. */
const FIXTURES: Record<string, unknown> = Object.fromEntries(
  Object.entries(
    import.meta.glob<unknown>("../fixtures/*.json", { eager: true, import: "default" }),
  ).map(([file, data]) => [file.replace(/^.*\/(.+)\.json$/, "$1"), data]),
);

export const RUN_ID = runId.trim();

/** A deep copy of `src/fixtures/<name>.json`, safe to mutate in a test. */
export function fixtures<T = unknown>(name: string): T {
  if (!(name in FIXTURES)) throw new Error(`no fixture ${name}.json`);
  return structuredClone(FIXTURES[name]) as T;
}

export type StubRequest = { url: string; body?: unknown; headers: Headers };

export type StubRoute = {
  method?: string;
  path: RegExp | string;
  body: unknown | ((req: StubRequest) => unknown);
  status?: number;
};

export type StubCall = { method: string; url: string; headers: Headers; body?: unknown };

/** Every request the stub served, in order; cleared by each `installFetchStub`. */
export const calls: StubCall[] = [];

const realFetch = globalThis.fetch;

function parse(body: BodyInit | null | undefined): unknown {
  if (typeof body !== "string") return body ?? undefined;
  try {
    return JSON.parse(body) as unknown;
  } catch {
    return body;
  }
}

function matches(route: StubRoute, method: string, url: string): boolean {
  if ((route.method ?? "GET").toUpperCase() !== method) return false;
  if (typeof route.path === "string") return url.split("?")[0] === route.path;
  return route.path.test(url);
}

function respond(status: number, body: unknown): Response {
  if (status === 204 || body === undefined) return new Response(null, { status });
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Replaces `fetch` with a router over `routes`; an unrouted request is a 404 error body. */
export function installFetchStub(routes: StubRoute[]): void {
  calls.length = 0;
  globalThis.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    const body = parse(init.body);
    calls.push({ method, url, headers, body });
    const route = routes.find((candidate) => matches(candidate, method, url));
    if (route === undefined) {
      return respond(404, {
        error: { kind: "not_found", message: `no stub for ${method} ${url}` },
      });
    }
    const payload =
      typeof route.body === "function"
        ? (route.body as (req: StubRequest) => unknown)({ url, body, headers })
        : route.body;
    return respond(route.status ?? 200, payload);
  };
}

export function restoreFetch(): void {
  globalThis.fetch = realFetch;
}
