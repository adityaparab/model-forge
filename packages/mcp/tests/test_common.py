from pathlib import Path

from modelforge_mcp.common import ServerSettings, ToolErrorPayload, settings_from_env
from modelforge_mcp.schemas import DatasetRef
from modelforge_mcp.servers.data_profiler import ProfileDatasetInput


def test_dataset_ref_string_forms() -> None:
    assert DatasetRef.parse("polish_bankruptcy") == DatasetRef(adapter="polish_bankruptcy")
    assert DatasetRef.parse("taiwan_bankruptcy@7").as_string() == "taiwan_bankruptcy@7"
    assert DatasetRef(adapter="x").as_string() == "x"


def test_settings_from_env_defaults_and_overrides(tmp_path: Path) -> None:
    assert settings_from_env({}) == ServerSettings(
        artifact_dir=Path("artifacts"), data_dir=Path("data"), templates_dir=Path("templates")
    )
    assert settings_from_env({"MODELFORGE_ARTIFACT_DIR": str(tmp_path)}).artifact_dir == tmp_path


def test_errors_are_typed_payloads(call, settings) -> None:
    r = call("data-profiler", "profile_dataset", ProfileDatasetInput(dataset_id="0" * 64))
    assert r.is_error
    payload = ToolErrorPayload.model_validate_json(r.content[0].text.split(": ", 1)[1])
    assert payload.code == "artifact_not_found"
