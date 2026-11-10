import type { Schema } from "../../api/client";

type EventRecord = Schema<"EventRecord">;

const SUMMARY_MAX = 96;

/** `key=value` for the payload's scalar fields, cut to one line; nested values are elided. */
function payloadSummary(payload: EventRecord["payload"]): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(payload)) {
    if (value === null || value === undefined) continue;
    if (typeof value === "object") continue;
    parts.push(`${key}=${String(value)}`);
  }
  const text = parts.join(" ");
  return text.length > SUMMARY_MAX ? `${text.slice(0, SUMMARY_MAX - 1)}…` : text;
}

function time(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toISOString().slice(11, 23);
}

const HEAD = ["Id", "Time", "Type", "Step", "Payload", "Prev hash", "Hash"];
const cell = "h-8 border-b border-border px-2 whitespace-nowrap";
const mono = `${cell} font-mono text-[0.75rem] tabular-nums`;

/** The run's hash-chained event log: each row's `prev_hash` is the row above's `hash`. */
export function EventChain({ events }: { events: EventRecord[] }) {
  return (
    <div className="max-h-[32rem] overflow-auto rounded-sm border border-border">
      <table aria-label="Event chain" className="w-full border-collapse text-left text-[0.8125rem]">
        <thead className="sticky top-0 bg-panel text-xs text-muted">
          <tr>
            {HEAD.map((label) => (
              <th key={label} scope="col" className={`${cell} font-medium`}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id} className="hover:bg-panel">
              <td className={`${mono} text-right`}>{event.id}</td>
              <td className={mono} title={event.ts}>
                {time(event.ts)}
              </td>
              <td className={mono}>{event.type}</td>
              <td className={`${mono} text-muted`}>{event.step_id ?? ""}</td>
              <td className={`${mono} max-w-[28rem] truncate text-muted`} title={payloadSummary(event.payload)}>
                {payloadSummary(event.payload)}
              </td>
              <td className={`${mono} text-muted`} title={event.prev_hash}>
                {event.prev_hash.slice(0, 12)}
              </td>
              <td className={mono} title={event.hash}>
                {event.hash.slice(0, 12)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
