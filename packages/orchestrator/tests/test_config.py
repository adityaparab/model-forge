from pathlib import Path

from modelforge_mcp.common import ServerSettings
from modelforge_orchestrator.config import load_settings, server_settings, setting_seeds


def test_defaults_and_env_and_dotenv(tmp_path: Path) -> None:
    s = load_settings({}, dotenv=None)
    assert (
        s.database_url == "postgresql://modelforge:modelforge@localhost:5432/modelforge"
        and s.tool_timeout_s == 600
        and s.mcp_command is None
    )
    assert s.artifact_dir == Path("./artifacts") and s.profile == "default"
    assert s.log_level == "INFO" and s.knowledge_dir == Path("./knowledge-base")
    (tmp_path / ".env").write_text(
        "MODELFORGE_PROFILE=ci-cheap\nMODELFORGE_MCP_COMMAND=uv run modelforge-mcp\n"
    )
    s = load_settings({"MODELFORGE_PROFILE": "default"}, dotenv=tmp_path / ".env")
    assert s.profile == "default" and s.mcp_command == ("uv", "run", "modelforge-mcp")


def test_dotenv_comments_quotes_and_missing_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "# comment\n\nMODELFORGE_TOOL_TIMEOUT_S=\"30\"\nMODELFORGE_DATA_DIR='/d'\n"
    )
    s = load_settings({}, dotenv=tmp_path / ".env")
    assert s.tool_timeout_s == 30.0 and s.data_dir == Path("/d")
    assert load_settings({}, dotenv=tmp_path / "absent.env").tool_timeout_s == 600


def test_server_settings_maps_directories() -> None:
    s = load_settings(
        {
            "MODELFORGE_ARTIFACT_DIR": "/a",
            "MODELFORGE_TEMPLATES_DIR": "/t",
            "MODELFORGE_KNOWLEDGE_DIR": "/k",
        },
        dotenv=None,
    )
    assert server_settings(s) == ServerSettings(
        artifact_dir=Path("/a"),
        data_dir=Path("./data"),
        templates_dir=Path("/t"),
        knowledge_dir=Path("/k"),
    )


def test_settings_gateway_from_env_and_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=abc\n")
    s = load_settings({"MODELFORGE_COST_CAP_USD": "3"}, dotenv=tmp_path / ".env")
    assert s.gateway.anthropic_api_key == "abc" and s.gateway.cost_cap_usd == 3.0
    assert s.knowledge_dir == Path("./knowledge-base")
    assert s.gateway.profiles_dir == Path("./profiles")
    assert load_settings({}, dotenv=None).gateway.anthropic_api_key is None


def test_auto_replay_env_parsing() -> None:
    assert load_settings({}, dotenv=None).auto_replay is False
    assert load_settings({"MODELFORGE_AUTO_REPLAY": "1"}, dotenv=None).auto_replay is True
    assert load_settings({"MODELFORGE_AUTO_REPLAY": "true"}, dotenv=None).auto_replay is True
    assert load_settings({"MODELFORGE_AUTO_REPLAY": "0"}, dotenv=None).auto_replay is False
    s = load_settings(
        {
            "MODELFORGE_PROFILE": "ci-cheap",
            "MODELFORGE_COST_CAP_USD": "3",
            "MODELFORGE_AUTO_REPLAY": "true",
        },
        dotenv=None,
    )
    assert setting_seeds(s) == {
        "default_profile": "ci-cheap",
        "default_cost_cap_usd": 3.0,
        "auto_replay": True,
    }
