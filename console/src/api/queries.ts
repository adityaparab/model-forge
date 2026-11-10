import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, type Schema } from "./client";

export type RunFilters = {
  limit?: number;
  offset?: number;
  status?: string;
  profile?: string;
  requester?: string;
  replays?: "include" | "exclude";
};

export type SubmitRun = { brief: string; profile?: string | null; cost_cap_usd?: number | null };

/** Request bodies: the generated types mark defaulted fields required; the server fills them. */
export type ApproveBody = Omit<Schema<"ApprovalRequest">, "comment"> & { comment?: string };
export type ReplayBody = Partial<Schema<"ReplayRequest">>;

export const runKeys = {
  all: ["runs"] as const,
  list: (filters: RunFilters) => ["runs", "list", filters] as const,
  run: (id: string) => ["runs", id] as const,
  part: (id: string, part: string, ...rest: unknown[]) => ["runs", id, part, ...rest] as const,
};

export const otherKeys = {
  config: ["config"] as const,
  briefs: ["briefs"] as const,
  inventory: ["inventory"] as const,
  costs: (days: number) => ["costs", days] as const,
  settings: ["settings"] as const,
};

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

const runPath = (id: string, rest = "") => `/runs/${encodeURIComponent(id)}${rest}`;

function useRunPart<T>(id: string, part: string) {
  return useQuery({
    queryKey: runKeys.part(id, part),
    queryFn: () => api<T>(runPath(id, `/${part}`)),
    enabled: id !== "",
  });
}

// --- reads -------------------------------------------------------------------------------

export function useConfig() {
  return useQuery({
    queryKey: otherKeys.config,
    queryFn: () => api<Schema<"ConfigResponse">>("/config"),
  });
}

export function useBriefs() {
  return useQuery({
    queryKey: otherKeys.briefs,
    queryFn: () => api<Schema<"BriefItem">[]>("/briefs"),
  });
}

export function useRuns(filters: RunFilters = {}) {
  return useQuery({
    queryKey: runKeys.list(filters),
    queryFn: () => api<Schema<"RunListResponse">>(`/runs${query(filters)}`),
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: runKeys.run(id),
    queryFn: () => api<Schema<"RunDetail">>(runPath(id)),
    enabled: id !== "",
  });
}

export const useSteps = (id: string) => useRunPart<Schema<"StepDetail">[]>(id, "steps");
export const useLlmCalls = (id: string) => useRunPart<Schema<"LlmCallsResponse">>(id, "llm-calls");
export const useArtifacts = (id: string) => useRunPart<Schema<"ArtifactItem">[]>(id, "artifacts");
export const useDocument = (id: string) => useRunPart<Schema<"DocumentResponse">>(id, "document");
export const useValidation = (id: string) =>
  useRunPart<Schema<"ValidationReport">>(id, "validation");
export const useFindings = (id: string) => useRunPart<Schema<"FindingsResponse">>(id, "findings");
export const useApprovals = (id: string) =>
  useRunPart<Schema<"ApprovalRecord">[]>(id, "approvals");
export const useReplays = (id: string) => useRunPart<Schema<"ReplayItem">[]>(id, "replays");
export const useManifest = (id: string) => useRunPart<Schema<"Manifest">>(id, "manifest");

/** The modelling plan stored at `planRef` (a JSON artifact), for editing at the model gate. */
export function usePlan(id: string, planRef: string | null) {
  return useQuery({
    queryKey: runKeys.part(id, "artifacts", planRef),
    queryFn: () =>
      api<Schema<"ModelingPlan">>(runPath(id, `/artifacts/${encodeURIComponent(planRef ?? "")}`)),
    enabled: id !== "" && planRef !== null,
  });
}

export function useEvents(id: string, after?: number) {
  return useQuery({
    queryKey: runKeys.part(id, "events", after),
    queryFn: () => api<Schema<"EventsResponse">>(runPath(id, `/events${query({ after })}`)),
    enabled: id !== "",
  });
}

export function useInventory() {
  return useQuery({
    queryKey: otherKeys.inventory,
    queryFn: () => api<Schema<"InventoryEntry">[]>("/inventory"),
  });
}

export function useCosts(days: number) {
  return useQuery({
    queryKey: otherKeys.costs(days),
    queryFn: () => {
      const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
      return api<Schema<"CostsResponse">>(`/costs${query({ since })}`);
    },
  });
}

export function useSettings() {
  return useQuery({
    queryKey: otherKeys.settings,
    queryFn: () => api<Schema<"SettingsResponse">>("/settings"),
  });
}

// --- writes ------------------------------------------------------------------------------

async function invalidateRun(client: QueryClient, id: string): Promise<void> {
  await Promise.all([
    client.invalidateQueries({ queryKey: runKeys.run(id) }),
    client.invalidateQueries({ queryKey: runKeys.all }),
  ]);
}

function useRunMutation<TBody, TResult>(id: string, send: (body: TBody) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: send,
    onSuccess: () => invalidateRun(client, id),
  });
}

export function useSubmitRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SubmitRun) =>
      api<Schema<"RunRecord">>("/runs", { method: "POST", json: body }),
    onSuccess: (run) => invalidateRun(client, run.id),
  });
}

export function useApprove(id: string) {
  return useRunMutation(id, (body: ApproveBody) =>
    api<Schema<"ApprovalRecord">>(runPath(id, "/approvals"), { method: "POST", json: body }),
  );
}

export function useResume(id: string) {
  return useRunMutation(id, (body: Schema<"ResumeRequest"> = {}) =>
    api<Schema<"RunRecord">>(runPath(id, "/resume"), { method: "POST", json: body }),
  );
}

export function usePause(id: string) {
  return useRunMutation<void, Schema<"RunRecord">>(id, () =>
    api<Schema<"RunRecord">>(runPath(id, "/pause"), { method: "POST" }),
  );
}

export function useAbort(id: string) {
  return useRunMutation<void, Schema<"RunRecord">>(id, () =>
    api<Schema<"RunRecord">>(runPath(id, "/abort"), { method: "POST" }),
  );
}

export function useReplay(id: string) {
  return useRunMutation(id, (body: ReplayBody = {}) =>
    api<Schema<"RunRecord">>(runPath(id, "/replay"), { method: "POST", json: body }),
  );
}

/** Verification runs on demand (it recomputes every hash), so it is a mutation over a GET. */
export function useVerify(id: string) {
  return useRunMutation<void, Schema<"VerifyReport">>(id, () =>
    api<Schema<"VerifyReport">>(runPath(id, "/verify")),
  );
}

export function usePatchSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: Schema<"SettingsPatch">) =>
      api<Schema<"SettingsResponse">>("/settings", { method: "PATCH", json: patch }),
    onSuccess: async (settings) => {
      client.setQueryData(otherKeys.settings, settings);
      await Promise.all([
        client.invalidateQueries({ queryKey: otherKeys.settings }),
        client.invalidateQueries({ queryKey: otherKeys.config }),
        client.invalidateQueries({ queryKey: runKeys.all }),
      ]);
    },
  });
}
