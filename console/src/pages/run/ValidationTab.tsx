import { useValidation } from "../../api/queries";
import type { Schema } from "../../api/client";
import { Mono } from "../../components/Mono";
import { useRunDetail } from "./useRunDetail";

type TestStatus = Schema<"ValidationTest">["status"];
type Overall = Schema<"ValidationReport">["overall"];

const TONE: Record<TestStatus, string> = {
  pass: "bg-status-completed text-bg",
  warn: "bg-status-awaiting text-bg",
  fail: "bg-status-attention text-text",
  not_applicable: "border border-border text-muted",
};

const pill = "inline-flex h-5 items-center rounded-sm px-2 text-xs font-medium whitespace-nowrap";

function TestStatusPill({ status }: { status: TestStatus }) {
  return <span className={`${pill} ${TONE[status]}`}>{status.replaceAll("_", " ")}</span>;
}

function OverallPill({ overall }: { overall: Overall }) {
  return (
    <span role="status" aria-label="Overall" className={`${pill} h-6 px-3 text-sm ${TONE[overall]}`}>
      {overall}
    </span>
  );
}

function formatValue(value: number | null): string {
  return value === null ? "n/a" : value.toFixed(4);
}

const head = "h-8 border-b border-border px-2 text-left text-xs font-medium text-muted";
const cell = "border-b border-border px-2 py-1.5 align-top";

/** The validation report: the overall band, every test against its threshold, the thresholds. */
export function ValidationTab() {
  const { run } = useRunDetail();
  const validation = useValidation(run.id);

  if (validation.isError) {
    return (
      <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
        Could not load the validation report: {validation.error.message}
      </p>
    );
  }
  if (validation.data === undefined) {
    return <p className="text-muted">Loading the validation report...</p>;
  }

  const { model_name, overall, tests, thresholds } = validation.data;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold">Validation</h2>
        <Mono>{model_name}</Mono>
        <span className="text-xs text-muted">Overall</span>
        <OverallPill overall={overall} />
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_16rem]">
        <div className="overflow-x-auto">
          <table aria-label="Validation tests" className="w-full border-collapse text-[0.8125rem]">
            <thead>
              <tr>
                <th className={head}>Test</th>
                <th className={head}>Name</th>
                <th className={head}>Status</th>
                <th className={`${head} text-right`}>Value</th>
                <th className={head}>Threshold</th>
                <th className={head}>Message</th>
              </tr>
            </thead>
            <tbody>
              {tests.map((test) => (
                <tr key={test.test_id} className="hover:bg-panel">
                  <td className={`${cell} whitespace-nowrap`}>
                    <Mono>{test.test_id}</Mono>
                  </td>
                  <td className={`${cell} whitespace-nowrap`}>{test.name}</td>
                  <td className={cell}>
                    <TestStatusPill status={test.status} />
                  </td>
                  <td className={`${cell} text-right whitespace-nowrap`}>
                    <Mono>{formatValue(test.value)}</Mono>
                  </td>
                  <td className={`${cell} text-xs text-muted`}>{test.threshold}</td>
                  <td className={`${cell} text-xs text-muted`}>{test.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <section aria-label="Thresholds" className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold text-muted">Thresholds</h3>
          <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 rounded-sm border border-border bg-panel p-3">
            {Object.entries(thresholds).map(([name, value]) => (
              <div key={name} className="contents">
                <dt className="font-mono text-xs text-muted">{name}</dt>
                <dd className="text-right">
                  <Mono>{String(value)}</Mono>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </div>
  );
}
