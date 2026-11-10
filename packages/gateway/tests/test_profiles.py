from pathlib import Path

import pytest
from modelforge_gateway.profiles import Route, load_profile
from modelforge_gateway.schemas import GatewayError


def test_shipped_profiles_load_and_route_every_purpose(repo_profiles_dir: Path) -> None:
    for name in ("default", "ci-cheap", "local-ollama", "local-lmstudio", "fake"):
        profile = load_profile(repo_profiles_dir, name)
        assert profile.name == name
        assert set(profile.routes) == {"scope", "document", "challenge", "remediate", "judge"}
    default = load_profile(repo_profiles_dir, "default")
    assert default.routes["document"] == Route(
        provider="anthropic", model="claude-opus-5-5", effort="high"
    )
    assert "claude-opus-5-5" in default.prices
    assert load_profile(repo_profiles_dir, "fake").routes["scope"].model == "scripted-v1"


def test_shipped_profile_details(repo_profiles_dir: Path) -> None:
    ci = load_profile(repo_profiles_dir, "ci-cheap")
    assert {p: r.effort for p, r in ci.routes.items()} == {
        "scope": "low",
        "document": "medium",
        "challenge": "medium",
        "remediate": "low",
        "judge": "low",
    }
    assert {r.model for r in ci.routes.values()} == {"claude-sonnet-5-5"}
    assert set(ci.prices) == {"claude-opus-5-5", "claude-sonnet-5-5"}
    for name, provider, model in (
        ("local-ollama", "ollama", "gpt-oss:20b"),
        ("local-lmstudio", "lmstudio", "google/gemma-4-26b-a4b-qat"),
        ("fake", "fake", "scripted-v1"),
    ):
        profile = load_profile(repo_profiles_dir, name)
        assert profile.prices == {}
        assert {(r.provider, r.model) for r in profile.routes.values()} == {(provider, model)}


def test_anthropic_profiles_route_scope_through_forced_tool_calls(
    repo_profiles_dir: Path,
) -> None:
    for name in ("default", "ci-cheap"):
        routes = load_profile(repo_profiles_dir, name).routes
        assert routes["scope"].structured == "function_calling"
        assert {p: r.structured for p, r in routes.items() if p != "scope"} == dict.fromkeys(
            ("document", "challenge", "remediate", "judge")
        )


def test_load_profile_errors_are_config(tmp_path: Path) -> None:
    with pytest.raises(GatewayError) as exc:
        load_profile(tmp_path, "nope")
    assert exc.value.kind == "config"
    with pytest.raises(GatewayError) as exc:
        load_profile(tmp_path, "../etc")
    assert exc.value.kind == "config"


def test_load_profile_missing_purpose_is_config(tmp_path: Path) -> None:
    (tmp_path / "partial.yaml").write_text(
        "name: partial\nroutes:\n  scope: {provider: fake, model: scripted-v1}\n"
    )
    with pytest.raises(GatewayError) as exc:
        load_profile(tmp_path, "partial")
    assert exc.value.kind == "config"
