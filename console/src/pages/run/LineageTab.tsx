import { useState, type ReactNode } from "react";

import { fetchEvents, useArtifacts, useEvents, useManifest, useVerify } from "../../api/queries";
import type { Schema } from "../../api/client";
import { EventChain } from "../../components/run/EventChain";
import { Mono } from "../../components/Mono";
import { useRunDetail } from "./useRunDetail";

type ArtifactItem = Schema<"ArtifactItem">;
type VerifyReport = Schema<"VerifyReport">;
type EventsResponse = Schema<"EventsResponse">;

/** Events per request; a page this full means the log may go on past it. */
const EVENTS_PAGE = 500;

const head = "h-8 border-b border-border px-2 text-left text-xs font-medium text-muted";
const cell = "h-8 border-b border-border px-2 whitespace-nowrap";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function LoadError({ what, message }: { what: string; message: string }) {
  return (
    <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
      Could not load {what}: {message}
    </p>
  );
}

function ArtifactsTable({ runId, artifacts }: { runId: string; artifacts: ArtifactItem[] }) {
  return (
    <div className="max-h-[32rem] overflow-auto rounded-sm border border-border">
      <table aria-label="Artifacts" className="w-full border-collapse text-[0.8125rem]">
        <thead className="sticky top-0 bg-panel">
          <tr>
            <th className={head}>Kind</th>
            <th className={head}>Ref names</th>
            <th className={head}>Media type</th>
            <th className={`${head} text-right`}>Size</th>
            <th className={head}>Id</th>
            <th className={head}>Content</th>
          </tr>
        </thead>
        <tbody>
          {artifacts.map((artifact) => (
            <tr key={artifact.id} className="hover:bg-panel">
              <td className={cell}>{artifact.kind}</td>
              <td className={`${cell} font-mono text-xs text-muted`}>{artifact.ref_names.join(", ")}</td>
              <td className={`${cell} font-mono text-xs text-muted`}>{artifact.media_type}</td>
              <td className={`${cell} text-right font-mono text-xs tabular-nums`}>
                {formatBytes(artifact.size_bytes)}
              </td>
              <td className={cell}>
                <Mono copy>{artifact.id}</Mono>
              </td>
              <td className={cell}>
                <a
                  href={`/api/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifact.id)}`}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-xs text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
                >
                  view
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-mono text-[0.8125rem] tabular-nums">{children}</dd>
    </div>
  );
}

function yesNo(value: boolean): string {
  return value ? "yes" : "no";
}

function listOrNone(ids: string[]): string {
  return ids.length === 0 ? "none" : ids.join(", ");
}

function VerifyResult({ report }: { report: VerifyReport }) {
  return (
    <section aria-label="Verify report" className="flex flex-col gap-2 rounded-sm border border-border bg-panel p-3">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted">Result</span>
        <span
          aria-label="Verify result"
          className={`inline-flex h-5 items-center rounded-sm px-2 text-xs font-medium ${
            report.ok ? "bg-status-completed text-bg" : "bg-status-attention text-text"
          }`}
        >
          {report.ok ? "ok" : "failed"}
        </span>
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
        <Fact label="Chain">
          {report.chain.ok ? "ok" : `broken at event ${String(report.chain.first_bad_id ?? "?")}`}
        </Fact>
        <Fact label="Events">{`${report.chain.events} events`}</Fact>
        <Fact label="Artifacts checked">{String(report.artifacts_checked)}</Fact>
        <Fact label="Missing">{listOrNone(report.artifacts_missing)}</Fact>
        <Fact label="Corrupt">{listOrNone(report.artifacts_corrupt)}</Fact>
        <Fact label="Manifest ok">{yesNo(report.manifest_ok)}</Fact>
        <Fact label="Head anchored">{yesNo(report.events_head_anchored)}</Fact>
      </dl>
    </section>
  );
}

function VerifyPanel({ runId, sealed }: { runId: string; sealed: boolean }) {
  const verify = useVerify(runId);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!sealed || verify.isPending}
          onClick={() => verify.mutate()}
          className="h-7 rounded-sm border border-border bg-panel-2 px-3 text-[0.8125rem] hover:border-accent disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent"
        >
          Verify chain
        </button>
        {!sealed && <span className="text-xs text-muted">Verification needs a sealed run.</span>}
        {verify.isPending && <span className="text-xs text-muted">Recomputing every hash...</span>}
      </div>
      {verify.isError && <LoadError what="the verify report" message={verify.error.message} />}
      {verify.data !== undefined && <VerifyResult report={verify.data} />}
    </div>
  );
}

function ManifestBlock({ runId }: { runId: string }) {
  const manifest = useManifest(runId);
  if (manifest.isError) return <LoadError what="the manifest" message={manifest.error.message} />;
  if (manifest.data === undefined) return <p className="text-muted">Loading the manifest...</p>;
  const { refs, tool_versions, approvals, outcome } = manifest.data;
  return (
    <section aria-label="Manifest" className="flex flex-col gap-3 rounded-sm border border-border bg-panel p-3">
      <h3 className="text-xs font-semibold text-muted">Manifest</h3>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
        <Fact label="Outcome">{outcome}</Fact>
        <Fact label="Refs">{String(Object.keys(refs).length)}</Fact>
      </dl>
      <div className="flex flex-col gap-1">
        <h4 className="text-xs text-muted">Approvals</h4>
        <ul className="flex flex-col gap-0.5 text-[0.8125rem]">
          {approvals.map((approval) => (
            <li key={approval.gate} className="font-mono text-xs">
              {approval.gate}: {approval.decision} by {approval.approver}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-col gap-1">
        <h4 className="text-xs text-muted">Tool versions</h4>
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-0.5">
          {Object.entries(tool_versions).map(([tool, version]) => (
            <div key={tool} className="contents">
              <dt className="font-mono text-xs text-muted">{tool}</dt>
              <dd className="text-right font-mono text-xs">{version}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

/**
 * The event log page by page: the first page from the query, later pages appended on demand,
 * so a long chain never reads as complete when it is not. Keyed by run id by its parent.
 */
function EventChainSection({ runId }: { runId: string }) {
  const first = useEvents(runId, undefined, EVENTS_PAGE);
  const [more, setMore] = useState<EventsResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  if (first.isError) return <LoadError what="the events" message={first.error.message} />;
  if (first.data === undefined) return <p className="text-muted">Loading the events...</p>;

  const pages = [first.data, ...more];
  const last = pages[pages.length - 1];
  const events = pages.flatMap((page) => page.events);
  const full = last.events.length === EVENTS_PAGE;

  async function loadMore() {
    setLoading(true);
    setMoreError(null);
    try {
      const page = await fetchEvents(runId, last.next_after, EVENTS_PAGE);
      setMore((loaded) => [...loaded, page]);
    } catch (error) {
      setMoreError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <p className="text-xs text-muted">
        {`Showing ${events.length} events${full ? ", more exist" : ""}`}
      </p>
      <EventChain events={events} />
      {moreError !== null && <LoadError what="more events" message={moreError} />}
      {full && (
        <button
          type="button"
          disabled={loading}
          onClick={() => void loadMore()}
          className="h-7 self-start rounded-sm border border-border bg-panel-2 px-3 text-[0.8125rem] hover:border-accent disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent"
        >
          Load more
        </button>
      )}
    </>
  );
}

/** Lineage: every stored artifact, the hash-chained event log, verification and the manifest. */
export function LineageTab() {
  const { run } = useRunDetail();
  const artifacts = useArtifacts(run.id);
  const sealed = run.manifest_hash !== null;

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="flex min-w-0 flex-col gap-5">
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">Artifacts</h2>
          {artifacts.isError && <LoadError what="the artifacts" message={artifacts.error.message} />}
          {artifacts.data === undefined && !artifacts.isError && (
            <p className="text-muted">Loading the artifacts...</p>
          )}
          {artifacts.data !== undefined && <ArtifactsTable runId={run.id} artifacts={artifacts.data} />}
        </section>
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">Event chain</h2>
          <EventChainSection key={run.id} runId={run.id} />
        </section>
      </div>
      <aside className="flex flex-col gap-4">
        <VerifyPanel runId={run.id} sealed={sealed} />
        {sealed && <ManifestBlock runId={run.id} />}
      </aside>
    </div>
  );
}
