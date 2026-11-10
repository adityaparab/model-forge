import os
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.runtime import Runtime
from langgraph.types import Command
from modelforge_gateway.gateway import Gateway
from modelforge_gateway.profiles import Price, Profile, load_profile
from modelforge_gateway.prompts import PromptRegistry
from modelforge_gateway.providers.fake import Fault, ScriptedChatModel
from modelforge_gateway.settings import GatewaySettings
from modelforge_lineage import runs
from modelforge_lineage.approvals import record_approval
from modelforge_lineage.db import Database
from modelforge_lineage.facade import Lineage
from modelforge_lineage.ids import new_id
from modelforge_lineage.models import GateDecision
from modelforge_lineage.queue import enqueue
from modelforge_lineage.store import MEDIA_YAML, ArtifactStore
from modelforge_lineage.testing import database, database_url, db
from modelforge_mcp.schemas import DatasetRef
from modelforge_orchestrator.agent_schemas import FindingResponseDraft, ReReviewDraft
from modelforge_orchestrator.briefs import BriefError, brief_hash, parse_brief
from modelforge_orchestrator.config import Settings
from modelforge_orchestrator.deps import Deps
from modelforge_orchestrator.nodes.build import build
from modelforge_orchestrator.nodes.challenge import challenge
from modelforge_orchestrator.nodes.document import document
from modelforge_orchestrator.nodes.features import features
from modelforge_orchestrator.nodes.intake import intake
from modelforge_orchestrator.nodes.profile import profile
from modelforge_orchestrator.nodes.remediate import remediate
from modelforge_orchestrator.nodes.scope import scope
from modelforge_orchestrator.nodes.split import split
from modelforge_orchestrator.nodes.validate import validate
from modelforge_orchestrator.schemas import RunState, apply_update
from modelforge_orchestrator.tools import InProcessToolClient
from modelforge_orchestrator.worker import run_once, setup_checkpointer
from pydantic import BaseModel

__all__ = [
    "REPO",
    "SYNTHETIC_BRIEF",
    "Cli",
    "RemediationStub",
    "apply_node",
    "assert_llm_outputs",
    "checkpointer",
    "cli",
    "completed_cli_run",
    "completed_run_id",
    "database",
    "database_url",
    "db",
    "deps",
    "deps_priced",
    "make_run",
    "prepared_state",
    "prepared_state_no_oot",
    "queued_run",
    "recording_runtime",
    "run_id_from",
    "runtime",
    "runtime_with_faults",
    "runtime_with_model",
    "scoped_state",
    "settings",
    "signed_off_state",
    "state_no_oot",
    "store",
    "synthetic_state",
    "validated_state",
]

REPO = Path(__file__).resolve().parents[3]
SYNTHETIC_BRIEF = REPO / "examples/briefs/synthetic.yaml"


@pytest.fixture
def settings(tmp_path: Path, database: Database) -> Settings:
    return Settings(
        database_url=database.url,
        artifact_dir=tmp_path / "artifacts",
        data_dir=Path(os.environ.get("MODELFORGE_DATA_DIR", REPO / "data")),
        templates_dir=REPO / "templates",
        profile="fake",
        mcp_command=None,
        tool_timeout_s=600.0,
        log_level="INFO",
        knowledge_dir=REPO / "knowledge-base",
        gateway=GatewaySettings(prompts_dir=REPO / "prompts", profiles_dir=REPO / "profiles"),
    )


Cli = Callable[..., "subprocess.CompletedProcess[str]"]
"""``cli(*args, env=None)`` runs ``uv run modelforge *args`` against the test database and
directories under the ``fake`` profile; ``env`` overrides the fixture environment."""


@pytest.fixture
def cli(settings: Settings, checkpointer: PostgresSaver) -> Cli:
    """Run the real ``modelforge`` entry point as a subprocess.

    A subprocess rather than Typer's `CliRunner`: the MCP stdio transport needs a real
    ``stderr`` file descriptor when it spawns the servers.
    """
    del checkpointer  # the checkpoint tables must exist before the CLI drives a run
    base_env = {
        **os.environ,
        "MODELFORGE_DATABASE_URL": settings.database_url,
        "MODELFORGE_ARTIFACT_DIR": str(settings.artifact_dir),
        "MODELFORGE_DATA_DIR": str(settings.data_dir),
        "MODELFORGE_TEMPLATES_DIR": str(settings.templates_dir),
        "MODELFORGE_PROFILE": settings.profile,
        "MODELFORGE_KNOWLEDGE_DIR": str(settings.knowledge_dir),
        "MODELFORGE_PROMPTS_DIR": str(settings.gateway.prompts_dir),
        "MODELFORGE_PROFILES_DIR": str(settings.gateway.profiles_dir),
    }

    def run(*args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["uv", "run", "modelforge", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            env={**base_env, **(env or {})},
            check=False,
        )

    return run


def run_id_from(text: str) -> str:
    """The first run id in ``text``."""
    found = re.search(r"run_[0-9a-f]{12}", text)
    if found is None:
        raise AssertionError(f"no run id in {text!r}")
    return found.group(0)


def assert_llm_outputs(status: Mapping[str, Any]) -> None:
    """The gateway-backed agents ran under the fake profile and left grounded outputs."""
    assert {c["purpose"] for c in status["llm_calls"]} >= {"scope", "document", "challenge"}
    refs = status["refs"]
    assert refs["document_sections"]["summary"]["n_flagged_sections"] == 0
    assert refs["completeness"]["summary"]["complete"] is True
    assert "scope_notes" in refs
    assert "knowledge_index" in refs


@pytest.fixture
def completed_cli_run(cli: Cli) -> str:
    """A synthetic run driven through the three gates with the CLI; return its id."""
    result = cli("run", str(SYNTHETIC_BRIEF), "--requester", "alice")
    assert result.returncode == 0, result.stderr
    run_id = run_id_from(result.stdout)
    for args in (
        ("scope", "--decision", "approve", "--approver", "alice"),
        ("model", "--decision", "accept", "--approver", "bob"),
        ("signoff", "--decision", "approve", "--approver", "bob"),
    ):
        result = cli("approve", run_id, *args)
        assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"{run_id} completed"
    return run_id


@pytest.fixture(scope="session")
def checkpointer(database: Database) -> Iterator[PostgresSaver]:
    yield setup_checkpointer(database)


def make_run(
    db: Database,
    store: ArtifactStore,
    brief_path: Path | None = None,
    *,
    brief_bytes: bytes | None = None,
    requester: str = "alice",
    replay_of: str | None = None,
) -> RunState:
    """Store the brief as a `brief` artifact, create the run row, and return the initial state.

    A brief that does not parse still gets a run (synthetic dataset, seed 42), so tests can
    exercise `intake` rejecting it.
    """
    if brief_bytes is None:
        if brief_path is None:
            raise ValueError("make_run needs brief_path or brief_bytes")
        brief_bytes = brief_path.read_bytes()
    ref = store.put(brief_bytes, kind="brief", media_type=MEDIA_YAML)
    try:
        dataset_ref = parse_brief(brief_bytes).dataset
    except BriefError:
        dataset_ref = DatasetRef(adapter="synthetic_credit")
    seed = dataset_ref.seed
    run_id = new_id("run")
    with db.transaction() as conn:
        runs.create_run(
            conn,
            run_id=run_id,
            requester=requester,
            profile="fake",
            brief_hash=brief_hash(brief_bytes),
            brief_ref=ref.id,
            dataset_ref=dataset_ref.as_string(),
            seed=seed,
            replay_of=replay_of,
        )
    return RunState(
        run_id=run_id,
        requester=requester,
        profile="fake",
        seed=seed,
        brief_hash=brief_hash(brief_bytes),
        brief_ref=ref.id,
        dataset_ref=dataset_ref,
        replay_of=replay_of,
        status="running",
    )


def queued_run(
    db: Database, store: ArtifactStore, brief_path: Path, *, requester: str = "alice"
) -> str:
    """`make_run` followed by enqueueing the run as a ``start``; return the run id."""
    run_id = make_run(db, store, brief_path, requester=requester).run_id
    with db.transaction() as conn:
        enqueue(conn, run_id, "start")
    return run_id


@pytest.fixture
def completed_run_id(
    settings: Settings, database: Database, store: ArtifactStore, checkpointer: PostgresSaver
) -> str:
    """A fresh synthetic run driven through the three gates by the worker; return its id."""
    del checkpointer  # the checkpoint tables must exist before the worker runs
    run_id = queued_run(database, store, SYNTHETIC_BRIEF)
    run_once(settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient)
    for decision in (
        GateDecision(gate="scope", decision="approve", approver="alice"),
        GateDecision(gate="model", decision="accept", approver="bob"),
        GateDecision(gate="signoff", decision="approve", approver="bob"),
    ):
        with database.transaction() as conn:
            record_approval(conn, run_id, decision, current_plan=None)
            enqueue(conn, run_id, "resume")
        run = run_once(
            settings, database, store, run_id=run_id, tool_client_factory=InProcessToolClient
        )
    assert run is not None and run.status == "completed"
    return run_id


@pytest.fixture
def store(settings: Settings) -> ArtifactStore:
    """The same directory the in-process MCP servers write to."""
    return ArtifactStore(settings.artifact_dir)


@pytest.fixture
def deps(settings: Settings, database: Database) -> Iterator[Deps]:
    store = ArtifactStore(settings.artifact_dir)
    with InProcessToolClient(settings) as client:
        yield Deps(
            tools=client,
            lineage=Lineage(database),
            store=store,
            settings=settings,
            gateway=Gateway.for_run(settings.gateway, "fake", Lineage(database), store),
        )


@pytest.fixture
def runtime(deps: Deps) -> Runtime[Deps]:
    """Enough of a LangGraph `Runtime` for calling nodes directly."""
    return cast("Runtime[Deps]", SimpleNamespace(context=deps))


def _deps_with_gateway(
    deps: Deps, profile: Profile, chat_model: BaseChatModel | None = None
) -> Deps:
    """``deps`` (sharing its tool client) with a gateway over ``profile``.

    ``chat_model``, when given, answers every route; otherwise the profile's providers do.
    """
    settings = deps.settings.gateway
    registry = PromptRegistry(settings.prompts_dir, deps.lineage)
    if chat_model is None:
        gateway = Gateway(settings, profile, deps.lineage, deps.store, registry)
    else:
        gateway = Gateway(
            settings,
            profile,
            deps.lineage,
            deps.store,
            registry,
            chat_model_factory=lambda route, s, n: chat_model,
        )
    return Deps(
        tools=deps.tools,
        lineage=deps.lineage,
        store=deps.store,
        settings=deps.settings,
        gateway=gateway,
    )


class RecordingChatModel(ScriptedChatModel):
    """The scripted model, appending every ``messages`` list it is asked to answer to
    ``captured`` before answering."""

    captured: list[list[BaseMessage]] = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.captured.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        inner = super().with_structured_output(schema, include_raw=include_raw, **kwargs)

        def respond(model_input: LanguageModelInput) -> Any:
            self.captured.append(self._convert_input(model_input).to_messages())
            return inner.invoke(model_input)

        return RunnableLambda(respond)


def recording_runtime(deps: Deps) -> tuple["Runtime[Deps]", list[list[BaseMessage]]]:
    """A `Runtime` over ``deps`` whose gateway answers with the scripted model under the
    ``fake`` profile, and the list every message list sent to that model is appended to."""
    model = RecordingChatModel(captured=[])
    profile = load_profile(deps.settings.gateway.profiles_dir, "fake")
    context = _deps_with_gateway(deps, profile, model)
    return cast("Runtime[Deps]", SimpleNamespace(context=context)), model.captured


class RemediationStub(ScriptedChatModel):
    """The scripted model with the developer's responses or the re-review's verdicts overridden.

    ``response`` answers every `FindingResponseDraft` request: ``"no_evidence"`` disputes with
    no evidence, ``"dispute_all"`` disputes citing ``[A:plan#model_name]``; ``None`` keeps the
    script. ``re_review_open`` turns every scripted re-review verdict to ``open``.
    """

    response: Literal["no_evidence", "dispute_all"] | None = None
    re_review_open: bool = False

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        inner = super().with_structured_output(schema, include_raw=True, **kwargs)

        def respond(model_input: LanguageModelInput) -> Any:
            parsed: Any
            if schema is FindingResponseDraft and self.response is not None:
                evidence = [] if self.response == "no_evidence" else ["[A:plan#model_name]"]
                parsed = FindingResponseDraft(
                    decision="dispute",
                    action="The plan shows the finding is wrong.",
                    evidence=evidence,
                )
            else:
                parsed = cast("dict[str, Any]", inner.invoke(model_input))["parsed"]
                if isinstance(parsed, ReReviewDraft) and self.re_review_open:
                    parsed = ReReviewDraft(
                        verdicts=[v.model_copy(update={"status": "open"}) for v in parsed.verdicts]
                    )
            raw = AIMessage(
                content=parsed.model_dump_json(),
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={"stop_reason": "end_turn"},
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(respond)


def apply_node(state: RunState, result: dict[str, Any] | Command[Any]) -> RunState:
    """``state`` after a node's result: a plain update or a `Command`'s update."""
    update = result.update if isinstance(result, Command) else result
    return apply_update(state, update or {})


RuntimeWithModel = Callable[[BaseChatModel], "Runtime[Deps]"]
RuntimeWithFaults = Callable[[dict[str, Fault]], "Runtime[Deps]"]


@pytest.fixture
def runtime_with_model(deps: Deps) -> RuntimeWithModel:
    """``runtime_with_model(model)``: a `Runtime` whose gateway answers every call with ``model``
    over the ``fake`` profile."""
    profile = load_profile(deps.settings.gateway.profiles_dir, "fake")

    def make(model: BaseChatModel) -> Runtime[Deps]:
        context = _deps_with_gateway(deps, profile, model)
        return cast("Runtime[Deps]", SimpleNamespace(context=context))

    return make


@pytest.fixture
def runtime_with_faults(runtime_with_model: RuntimeWithModel) -> RuntimeWithFaults:
    """``runtime_with_faults(faults)``: the scripted model with ``faults`` keyed by section id."""

    def make(faults: dict[str, Fault]) -> Runtime[Deps]:
        return runtime_with_model(ScriptedChatModel(faults=faults))

    return make


@pytest.fixture
def deps_priced(deps: Deps) -> Deps:
    """``deps`` with a gateway over the ``fake`` routes priced at 4 and 20 USD per Mtok."""
    fake = load_profile(deps.settings.gateway.profiles_dir, "fake")
    priced = Profile(
        name="fake-priced",
        routes=fake.routes,
        prices={"scripted-v1": Price(input=4.0, output=20.0)},
    )
    return _deps_with_gateway(deps, priced)


@pytest.fixture
def synthetic_state(db: Database, store: ArtifactStore) -> RunState:
    return make_run(db, store, SYNTHETIC_BRIEF)


@pytest.fixture
def state_no_oot(db: Database, store: ArtifactStore, tmp_path: Path) -> RunState:
    text = SYNTHETIC_BRIEF.read_text()
    assert "oot_vintages: [5]" in text
    path = tmp_path / "synthetic-no-oot.yaml"
    path.write_text(text.replace("oot_vintages: [5]", "oot_vintages: []"))
    return make_run(db, store, path)


@pytest.fixture
def scoped_state(synthetic_state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``synthetic_state`` after `intake` and `scope`."""
    state = synthetic_state
    for node in (intake, scope):
        state = apply_update(state, node(state, runtime))
    return state


def _prepare(state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``state`` after `intake`, `scope`, `profile` and `split`, each update applied in turn."""
    for node in (intake, scope, profile, split):
        state = apply_update(state, node(state, runtime))
    return state


@pytest.fixture
def prepared_state(synthetic_state: RunState, runtime: Runtime[Deps]) -> RunState:
    return _prepare(synthetic_state, runtime)


@pytest.fixture
def prepared_state_no_oot(state_no_oot: RunState, runtime: Runtime[Deps]) -> RunState:
    return _prepare(state_no_oot, runtime)


@pytest.fixture
def validated_state(prepared_state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``prepared_state`` after `features`, `build` and `validate`."""
    state = prepared_state
    for node in (features, build, validate):
        state = apply_update(state, node(state, runtime))
    return state


@pytest.fixture
def signed_off_state(validated_state: RunState, runtime: Runtime[Deps]) -> RunState:
    """``validated_state`` after `document`, `challenge`, `remediate` and `challenge` again, so
    every finding has its final status, signed off."""
    state = validated_state
    for node in (document, challenge, remediate, challenge):
        state = apply_node(state, node(state, runtime))
    signoff = GateDecision(
        gate="signoff",
        decision="approve_with_conditions",
        approver="bob",
        conditions=["monitor PSI quarterly"],
    )
    return apply_update(state, {"gates": {"signoff": signoff}, "status": "completed"})
