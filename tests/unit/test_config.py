from app.core.config import AppSettings


def test_defaults_are_safe():
    s = AppSettings(_env_file=None)
    assert s.max_upload_bytes == 5 * 1024 * 1024
    assert s.allowed_extensions == (".json",)
    assert s.error_misses_path is None


def test_extensions_parsed_from_csv_string():
    s = AppSettings(_env_file=None, allowed_extensions="json, .yaml ,txt")
    assert s.allowed_extensions == (".json", ".yaml", ".txt")
