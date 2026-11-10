import { useApprove, useConfig, useSteps } from "../../api/queries";
import { ApprovalsList } from "../../components/run/ApprovalsList";
import { AttentionPanel } from "../../components/run/AttentionPanel";
import { GatePanel } from "../../components/run/GatePanel";
import { LiveFeed } from "../../components/run/LiveFeed";
import { ReplaysPanel } from "../../components/run/ReplaysPanel";
import { Timeline } from "../../components/run/Timeline";
import { useRunEventsFeed } from "../../events/runEventsContext";
import { useRunDetail } from "./useRunDetail";

function TimelineSection({ runId }: { runId: string }) {
  const steps = useSteps(runId);
  const config = useConfig();
  if (steps.isError || config.isError) {
    return (
      <p role="alert" className="rounded-sm border border-status-attention px-3 py-2 text-sm">
        Could not load the timeline: {(steps.error ?? config.error)?.message}
      </p>
    );
  }
  if (steps.data === undefined || config.data === undefined) {
    return <p className="text-muted">Loading the timeline...</p>;
  }
  return <Timeline steps={steps.data} nodes={config.data.nodes} />;
}

/** The run at a glance: the gate or error that needs a human, the timeline, the record. */
export function OverviewTab() {
  const detail = useRunDetail();
  const events = useRunEventsFeed();
  const approve = useApprove(detail.run.id);
  const { run, gate } = detail;

  return (
    <div className="flex flex-col gap-5">
      {gate !== null && (
        <GatePanel key={`${run.id}:${gate.gate}`} detail={detail} onDecide={approve.mutateAsync} />
      )}
      {run.status === "needs_attention" && <AttentionPanel run={run} />}
      <TimelineSection runId={run.id} />
      <LiveFeed events={events} />
      <ApprovalsList approvals={detail.approvals} />
      <ReplaysPanel replays={detail.replays} />
    </div>
  );
}
