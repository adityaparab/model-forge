from pathlib import Path

from modelforge_gateway.settings import GatewaySettings, gateway_settings_from_env


def test_settings_from_env_ignores_inherited_anthropic_base_url() -> None:
    s = gateway_settings_from_env(
        {
            "ANTHROPIC_BASE_URL": "http://proxy",
            "ANTHROPIC_API_KEY": "k",
            "MODELFORGE_COST_CAP_USD": "2.5",
        }
    )
    assert s.anthropic_base_url is None and s.anthropic_api_key == "k" and s.cost_cap_usd == 2.5
    assert (
        gateway_settings_from_env({"MODELFORGE_ANTHROPIC_BASE_URL": "http://x"}).anthropic_base_url
        == "http://x"
    )


def test_settings_defaults_and_empty_strings_count_as_unset() -> None:
    assert gateway_settings_from_env({}) == GatewaySettings()
    s = gateway_settings_from_env(
        {"ANTHROPIC_API_KEY": "", "MODELFORGE_OLLAMA_BASE_URL": "", "MODELFORGE_LLM_TIMEOUT_S": ""}
    )
    assert s == GatewaySettings()


def test_settings_reads_every_variable() -> None:
    s = gateway_settings_from_env(
        {
            "MODELFORGE_PROMPTS_DIR": "/p",
            "MODELFORGE_PROFILES_DIR": "/q",
            "MODELFORGE_OLLAMA_BASE_URL": "http://o",
            "MODELFORGE_OLLAMA_MODEL": "om",
            "MODELFORGE_LMSTUDIO_BASE_URL": "http://l",
            "MODELFORGE_LMSTUDIO_MODEL": "lm",
            "MODELFORGE_LLM_TIMEOUT_S": "12",
            "MODELFORGE_OLLAMA_NUM_CTX": "65536",
        }
    )
    assert s.prompts_dir == Path("/p") and s.profiles_dir == Path("/q")
    assert (s.ollama_base_url, s.ollama_model) == ("http://o", "om")
    assert (s.lmstudio_base_url, s.lmstudio_model) == ("http://l", "lm")
    assert s.request_timeout_s == 12.0
    assert s.ollama_num_ctx == 65536
    assert GatewaySettings().ollama_num_ctx == 32768
