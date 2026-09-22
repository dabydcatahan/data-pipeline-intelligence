"""Tests for opt-in unrecognized-error capture (F025)."""

import json

from app.insights.error_feedback import record_unrecognized_error, redact_text


def test_redacts_connection_string_account_key():
    text = (
        "DefaultEndpointsProtocol=https;AccountName=mystorage;"
        "AccountKey=abcd1234EFGH==;EndpointSuffix=core.windows.net"
    )
    redacted = redact_text(text)
    assert "abcd1234EFGH" not in redacted
    assert "AccountKey=***redacted***" in redacted
    assert "mystorage" in redacted  # non-secret context is preserved


def test_redacts_bearer_token():
    redacted = redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 failed")
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted
    assert "Bearer ***redacted***" in redacted


def test_redacts_sas_signature_query_param():
    redacted = redact_text(
        "https://mystorage.blob.core.windows.net/c/f.csv?sv=2020&sig=AbCdEf12345%3D&se=2026"
    )
    assert "AbCdEf12345" not in redacted
    assert "sig=***redacted***" in redacted
    assert "sv=2020" in redacted


def test_redacts_quoted_password_keyword():
    redacted = redact_text("password='SuperSecret123!' authentication failed")
    assert "SuperSecret123" not in redacted
    assert "password=***redacted***" in redacted


def test_leaves_non_secret_text_unchanged():
    text = "Column 'customer_id' not found in source data"
    assert redact_text(text) == text


def test_record_unrecognized_error_writes_redacted_jsonl(tmp_path):
    path = tmp_path / "misses" / "unrecognized.jsonl"
    record_unrecognized_error("password=hunter2 something weird happened", path)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "hunter2" not in record["error_text"]
    assert "flagged_at" in record


def test_record_unrecognized_error_appends(tmp_path):
    path = tmp_path / "unrecognized.jsonl"
    record_unrecognized_error("first error", path)
    record_unrecognized_error("second error", path)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
