"""`profile`: load the dataset, profile it, screen for leakage, and measure OOT drift."""

from typing import Any

from langgraph.runtime import Runtime
from modelforge_core.schemas import ProfileArtifact
from modelforge_lineage import runs
from modelforge_lineage.models import ArtifactRef
from modelforge_mcp.servers.data_profiler import (
    CheckLeakageInput,
    ComputePsiInput,
    LoadDatasetInput,
    LoadDatasetOutput,
    ProfileDatasetInput,
)

from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes import plan_of
from modelforge_orchestrator.schemas import RefName, RunState
from modelforge_orchestrator.steps import Step

SERVER = "data-profiler"


def profile(state: RunState, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    with Step(deps, state, "profile") as step:
        plan = plan_of(state)
        load = step.tool(SERVER, "load_dataset", LoadDatasetInput(dataset_ref=state.dataset_ref))
        loaded: LoadDatasetOutput = load.output
        dataset_id = loaded.dataset.id
        with deps.lineage.transaction() as conn:
            runs.set_dataset_id(conn, state.run_id, dataset_id)
        name = state.dataset_ref.as_string()
        prof = step.tool(
            SERVER,
            "profile_dataset",
            ProfileDatasetInput(dataset_id=dataset_id, dataset_name=name),
        )
        leak = step.tool(
            SERVER, "check_leakage", CheckLeakageInput(dataset_id=dataset_id, dataset_name=name)
        )
        refs: dict[RefName, ArtifactRef] = {
            "dataset": loaded.dataset,
            "dictionary": loaded.dictionary,
            "profile": prof.refs[0],
            "leakage": leak.refs[0],
        }
        oot = plan.vintage_policy.oot_vintages
        if not oot:
            step.event("profile.psi_skipped", {"reason": "the plan lists no OOT vintages"})
        elif not loaded.oot_capable:
            step.event("profile.psi_skipped", {"reason": "the dataset is not OOT-capable"})
        else:
            report = deps.store.get_model(refs["profile"].id, ProfileArtifact)
            psi = step.tool(
                SERVER,
                "compute_psi",
                ComputePsiInput(
                    dataset_id=dataset_id,
                    base_vintages=sorted(set(report.rows_by_vintage) - set(oot)),
                    compare_vintages=sorted(oot),
                ),
            )
            refs["psi"] = psi.refs[0]
        return step.complete({"refs": refs})
