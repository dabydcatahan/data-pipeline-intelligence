from app.core.config import AppSettings


def test_defaults_are_safe():
    s = AppSettings(_env_file=None)
    assert s.max_upload_bytes == 5 * 1024 * 1024
    assert s.allowed_extensions == (".json",)
    assert s.enable_llm_insights is False
    assert s.llm_ready() is False


def test_extensions_parsed_from_csv_string():
    s = AppSettings(_env_file=None, allowed_extensions="json, .yaml ,txt")
    assert s.allowed_extensions == (".json", ".yaml", ".txt")


def test_llm_ready_requires_full_config():
    s = AppSettings(
        _env_file=None,
        enable_llm_insights=True,
        llm_provider="anthropic",
        llm_model="claude-x",
        llm_api_key="k",
    )
    assert s.llm_ready() is True

    s2 = AppSettings(_env_file=None, enable_llm_insights=True, llm_provider="anthropic")
    assert s2.llm_ready() is False
