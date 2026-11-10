import { Outlet, useParams } from "react-router";

import { useRun } from "../api/queries";
import { RunHeader } from "../components/run/RunHeader";
import { RunEventsContext } from "../events/runEventsContext";
import { useRunEvents } from "../events/useRunEvents";

/** One run: the header and actions, kept live by the run's event stream, and the routed tab. */
export function RunPage() {
  const { runId = "" } = useParams();
  const detail = useRun(runId);
  const { events } = useRunEvents(runId);

  if (detail.isError) {
    return (
      <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
        Could not load run {runId}: {detail.error.message}
      </p>
    );
  }
  if (detail.data === undefined) return <p className="text-muted">Loading run...</p>;

  return (
    <div className="flex flex-col gap-4">
      <RunHeader detail={detail.data} />
      <RunEventsContext value={events}>
        <Outlet context={detail.data} />
      </RunEventsContext>
    </div>
  );
}
