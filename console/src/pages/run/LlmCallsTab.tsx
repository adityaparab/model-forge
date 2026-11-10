import { useLlmCalls } from "../../api/queries";
import type { Schema } from "../../api/client";
import { Mono } from "../../components/Mono";
import { useRunDetail } from "./useRunDetail";

type CostBucket = Schema<"CostBucket">;
type LlmCallRecord = Schema<"LlmCallRecord">;

const count = new Intl.NumberFormat("en-US");

function usd(value: number, digits = 2): string {
  return `$${value.toFixed(digits)}`;
}

const head = "h-8 border-b border-border px-2 text-left text-xs font-medium whitespace-nowrap text-muted";
const cell = "h-8 border-b border-border px-2 whitespace-nowrap";
const num = `${cell} text-right font-mono text-[0.75rem] tabular-nums`;

function BucketTable({
  label,
  keyLabel,
  buckets,
}: {
  label: string;
  keyLabel: string;
  buckets: Record<string, CostBucket>;
}) {
  return (
    <table aria-label={label} className="w-full border-collapse border border-border text-[0.8125rem]">
      <thead className="bg-panel">
        <tr>
          <th className={head}>{keyLabel}</th>
          <th className={`${head} text-right`}>Calls</th>
          <th className={`${head} text-right`}>Input</th>
          <th className={`${head} text-right`}>Output</th>
          <th className={`${head} text-right`}>Cache read</th>
          <th className={`${head} text-right`}>Cache write</th>
          <th className={`${head} text-right`}>Cost</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(buckets).map(([name, bucket]) => (
          <tr key={name} className="hover:bg-panel">
            <td className={`${cell} font-mono text-[0.75rem]`}>{name}</td>
            <td className={num}>{count.format(bucket.n_calls)}</td>
            <td className={num}>{count.format(bucket.input_tokens)}</td>
            <td className={num}>{count.format(bucket.output_tokens)}</td>
            <td className={num}>{count.format(bucket.cache_read_tokens)}</td>
            <td className={num}>{count.format(bucket.cache_write_tokens)}</td>
            <td className={num}>{usd(bucket.cost_usd, 4)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const CALL_HEAD: { label: string; numeric?: boolean }[] = [
  { label: "Step" },
  { label: "Purpose" },
  { label: "Provider" },
  { label: "Model" },
  { label: "Prompt" },
  { label: "Input", numeric: true },
  { label: "Output", numeric: true },
  { label: "Cache r / w", numeric: true },
  { label: "Cost", numeric: true },
  { label: "Latency", numeric: true },
  { label: "Stop reason" },
  { label: "Schema valid" },
];

function CallsTable({ calls }: { calls: LlmCallRecord[] }) {
  return (
    <div className="max-h-[36rem] overflow-auto rounded-sm border border-border">
      <table aria-label="LLM calls" className="w-full border-collapse text-[0.8125rem]">
        <thead className="sticky top-0 bg-panel">
          <tr>
            {CALL_HEAD.map(({ label, numeric }) => (
              <th key={label} className={`${head} ${numeric === true ? "text-right" : ""}`}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <tr key={call.id} className="hover:bg-panel">
              <td className={cell}>
                <Mono>{call.step_id}</Mono>
              </td>
              <td className={cell}>{call.purpose}</td>
              <td className={`${cell} text-muted`}>{call.provider}</td>
              <td className={`${cell} font-mono text-[0.75rem]`}>{call.model}</td>
              <td className={`${cell} font-mono text-[0.75rem]`}>
                {`${call.prompt_name} v${call.prompt_version}`}
              </td>
              <td className={num}>{count.format(call.input_tokens)}</td>
              <td className={num}>{count.format(call.output_tokens)}</td>
              <td className={num}>
                {`${count.format(call.cache_read_tokens)} / ${count.format(call.cache_write_tokens)}`}
              </td>
              <td className={num}>{usd(call.cost_usd, 4)}</td>
              <td className={num}>{`${count.format(call.latency_ms)} ms`}</td>
              <td className={`${cell} font-mono text-[0.75rem] text-muted`}>{call.stop_reason ?? ""}</td>
              <td
                className={`${cell} text-xs ${call.schema_valid ? "text-status-completed" : "text-status-attention"}`}
              >
                {call.schema_valid ? "yes" : "no"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What the run spent on models: against its cap, by purpose and by model, then call by call. */
export function LlmCallsTab() {
  const { run } = useRunDetail();
  const llm = useLlmCalls(run.id);

  if (llm.isError) {
    return (
      <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
        Could not load the LLM calls: {llm.error.message}
      </p>
    );
  }
  if (llm.data === undefined) return <p className="text-muted">Loading the LLM calls...</p>;

  const { calls, cost } = llm.data;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 rounded-sm border border-border bg-panel px-3 py-2">
        <span className="text-xs text-muted">Spent / cap</span>
        <span aria-label="Spend" className="font-mono text-sm tabular-nums">
          {`${usd(cost.spent_usd)} / ${usd(cost.cap_usd)}`}
        </span>
        <span className="text-xs text-muted">
          calls <Mono>{count.format(cost.n_calls)}</Mono>
        </span>
        <span className="text-xs text-muted">
          schema retries <Mono>{count.format(cost.n_schema_retries)}</Mono>
        </span>
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">By purpose</h2>
          <BucketTable label="Cost by purpose" keyLabel="Purpose" buckets={cost.by_purpose} />
        </section>
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">By model</h2>
          <BucketTable label="Cost by model" keyLabel="Model" buckets={cost.by_model} />
        </section>
      </div>
      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Calls</h2>
        <CallsTable calls={calls} />
      </section>
    </div>
  );
}
