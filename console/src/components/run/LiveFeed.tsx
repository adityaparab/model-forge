import type { Schema } from "../../api/client";

type EventRecord = Schema<"EventRecord">;

function time(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleTimeString();
}

/** The latest events from the run stream, newest first. */
export function LiveFeed({ events }: { events: EventRecord[] }) {
  return (
    <section aria-label="Live events" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">
        Live events{" "}
        <span className="font-mono text-xs font-normal text-muted">{events.length}</span>
      </h2>
      {events.length === 0 ? (
        <p className="text-xs text-muted">No events since this page opened.</p>
      ) : (
        <ol className="max-h-64 overflow-auto border border-border font-mono text-xs">
          {[...events].reverse().map((event) => (
            <li
              key={event.id}
              className="grid h-6 grid-cols-[4rem_6rem_12rem_1fr] items-center gap-2 border-b border-border px-2 last:border-b-0"
            >
              <span className="text-muted tabular-nums">#{event.id}</span>
              <time dateTime={event.ts} className="text-muted tabular-nums">
                {time(event.ts)}
              </time>
              <span>{event.type}</span>
              <span className="truncate text-muted">
                {typeof event.payload.node === "string" ? event.payload.node : ""}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
