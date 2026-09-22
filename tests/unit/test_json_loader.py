import pytest
from app.core.exceptions import (
    FileTooLargeError,
    InvalidJsonError,
    UnsupportedFileTypeError,
)
from app.models.artifact import ArtifactKind
from app.parsers.json_loader import detect_artifact_kind, load_adf_json


def test_load_valid_pipeline(read_fixture_bytes, settings):
    obj = load_adf_json(
        read_fixture_bytes("pipeline_copy_direct.json"), filename="p.json", settings=settings
    )
    assert obj["name"] == "CopyCustomerPipeline"


def test_rejects_bad_extension(settings):
    with pytest.raises(UnsupportedFileTypeError):
        load_adf_json(b"{}", filename="pipeline.txt", settings=settings)


def test_rejects_oversize(settings):
    big = b"{" + b" " * (settings.max_upload_bytes + 1) + b"}"
    with pytest.raises(FileTooLargeError):
        load_adf_json(big, filename="p.json", settings=settings)


def test_rejects_non_utf8(settings):
    with pytest.raises(InvalidJsonError):
        load_adf_json(b"\xff\xfe\x00bad", filename="p.json", settings=settings)


def test_rejects_invalid_json(read_fixture_bytes, settings):
    with pytest.raises(InvalidJsonError):
        load_adf_json(
            read_fixture_bytes("not_json.txt"), filename="not_json.json", settings=settings
        )


def test_rejects_non_object_top_level(settings):
    with pytest.raises(InvalidJsonError):
        load_adf_json(b"[1, 2, 3]", filename="p.json", settings=settings)


def test_rejects_empty(settings):
    with pytest.raises(InvalidJsonError):
        load_adf_json(b"   ", filename="p.json", settings=settings)


def test_rejects_deeply_nested_json_without_crashing(settings):
    """A small file (well under max_upload_bytes) can still nest deeply enough to
    exceed Python's recursion limit inside json.loads(). This must raise a clean
    InvalidJsonError, not an uncaught RecursionError (security review finding)."""
    depth = 150_000
    deeply_nested = b'{"a":' * depth + b"1" + b"}" * depth
    assert len(deeply_nested) < settings.max_upload_bytes
    with pytest.raises(InvalidJsonError, match="too deeply nested"):
        load_adf_json(deeply_nested, filename="deep.json", settings=settings)


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("pipeline_copy_direct.json", ArtifactKind.PIPELINE),
        ("pipeline_arm_form.json", ArtifactKind.PIPELINE),
        ("pipeline_multi_activity.json", ArtifactKind.PIPELINE),
        ("dataflow_select.json", ArtifactKind.MAPPING_DATA_FLOW),
        ("unknown_artifact.json", ArtifactKind.UNKNOWN),
    ],
)
def test_detect_artifact_kind(read_fixture_bytes, settings, fixture, expected):
    obj = load_adf_json(read_fixture_bytes(fixture), filename=fixture, settings=settings)
    assert detect_artifact_kind(obj) is expected
