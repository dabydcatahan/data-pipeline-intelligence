import json

from app.parsers.base import scrub_secrets
from app.parsers.pipeline_parser import PipelineParser


def _parse(read_fixture_bytes, name):
    obj = json.loads(read_fixture_bytes(name))
    return PipelineParser().parse(obj, source_filename=name)


def test_parse_direct_copy(read_fixture_bytes):
    pipeline, ctx = _parse(read_fixture_bytes, "pipeline_copy_direct.json")
    assert pipeline.name == "CopyCustomerPipeline"
    assert pipeline.activity_count == 1
    act = pipeline.activities[0]
    assert act.type == "Copy"
    assert [r.name for r in act.inputs] == ["CustomerSource"]
    assert [r.name for r in act.outputs] == ["CustomerSink"]
    assert pipeline.annotations == ("customer", "bronze")
    assert ctx.warnings == ()


def test_parse_multi_activity_dependencies_and_order(read_fixture_bytes):
    pipeline, _ = _parse(read_fixture_bytes, "pipeline_multi_activity.json")
    assert pipeline.folder == "Customer"
    assert pipeline.concurrency == 2
    assert {p.name for p in pipeline.parameters} == {"runDate", "batchSize"}
    assert {v.name for v in pipeline.variables} == {"rowCount"}

    order = pipeline.execution_order()
    assert order.order == ("CopyRaw", "TransformCustomer", "RefreshStats")
    assert order.has_cycle is False
    assert pipeline.data_flow_references == ("CustomerDataFlow",)
    assert pipeline.dependency_edges() == (
        ("CopyRaw", "TransformCustomer"),
        ("TransformCustomer", "RefreshStats"),
    )


def test_secrets_are_scrubbed_from_type_properties(read_fixture_bytes):
    pipeline, _ = _parse(read_fixture_bytes, "pipeline_multi_activity.json")
    copy = pipeline.activity("CopyRaw")
    assert copy is not None
    blob = json.dumps(copy.type_properties)
    assert "secret" not in blob.lower() or "redacted" in blob.lower()
    assert copy.type_properties["sink"]["connectionString"] == "***redacted***"


def test_arm_name_is_unwrapped(read_fixture_bytes):
    pipeline, _ctx = _parse(read_fixture_bytes, "pipeline_arm_form.json")
    assert pipeline.name == "OrdersPipeline"
    assert pipeline.execution_order().order == ("WaitABit", "CopyOrders")


def test_cycle_is_reported_not_raised(read_fixture_bytes):
    pipeline, ctx = _parse(read_fixture_bytes, "pipeline_cyclic.json")
    order = pipeline.execution_order()
    assert order.has_cycle is True
    assert set(order.cyclic) == {"A", "B"}
    assert any("Circular" in w for w in ctx.warnings)


def test_unknown_activity_is_kept_and_flagged(read_fixture_bytes):
    pipeline, ctx = _parse(read_fixture_bytes, "pipeline_unknown_activity.json")
    assert pipeline.activity_count == 2
    exotic = pipeline.activity("DoSomethingExotic")
    assert exotic is not None and exotic.is_recognized is False
    assert any("unrecognized type" in w for w in ctx.warnings)


def test_missing_activities_array_is_non_fatal(read_fixture_bytes):
    pipeline, ctx = PipelineParser().parse(
        {"name": "Empty", "properties": {}}, source_filename="e.json"
    )
    assert pipeline.name == "Empty"
    assert pipeline.activity_count == 0
    assert any("no 'activities'" in w for w in ctx.warnings)


def test_scrub_secrets_is_recursive():
    cleaned = scrub_secrets({"a": {"password": "x", "keep": [{"token": "y"}]}})
    assert cleaned == {"a": {"password": "***redacted***", "keep": [{"token": "***redacted***"}]}}


def test_control_flow_activities_are_expanded(read_fixture_bytes):
    pipeline, ctx = _parse(read_fixture_bytes, "pipeline_control_flow.json")
    assert ctx.warnings == ()

    names = {a.name for a in pipeline.activities}
    assert names == {
        "ForEachRegion",
        "CopyRegionRaw",
        "CheckHighVolume",
        "RunHighVolumeFlow",
        "RunLowVolumeFlow",
        "RouteByTier",
        "CopyGoldTier",
        "CopySilverTier",
        "CopyDefaultTier",
    }
    # The container itself, plus every nested activity, are all counted.
    assert pipeline.activity_count == 9

    top_level = pipeline.activity("ForEachRegion")
    assert top_level is not None
    assert top_level.parent_activity is None
    assert top_level.is_nested is False

    loop_body = pipeline.activity("CopyRegionRaw")
    assert loop_body is not None
    assert loop_body.parent_activity == "ForEachRegion"
    assert loop_body.container_branch == "loop"
    assert loop_body.is_nested is True
    assert [r.name for r in loop_body.outputs] == ["StageRegion"]

    if_true = pipeline.activity("RunHighVolumeFlow")
    assert if_true is not None
    assert if_true.parent_activity == "CheckHighVolume"
    assert if_true.container_branch == "true"
    assert if_true.data_flow_reference == "HighVolumeEnrichFlow"

    if_false = pipeline.activity("RunLowVolumeFlow")
    assert if_false is not None
    assert if_false.container_branch == "false"

    gold = pipeline.activity("CopyGoldTier")
    assert gold is not None
    assert gold.parent_activity == "RouteByTier"
    assert gold.container_branch == "case:gold"

    default = pipeline.activity("CopyDefaultTier")
    assert default is not None
    assert default.container_branch == "default"

    # data_flow_references picks up ExecuteDataFlow activities nested inside the If.
    assert set(pipeline.data_flow_references) == {"HighVolumeEnrichFlow", "LowVolumeEnrichFlow"}


def test_control_flow_copy_lineage_is_visible(read_fixture_bytes):
    from app.lineage.pipeline_lineage import build_pipeline_lineage

    pipeline, _ = _parse(read_fixture_bytes, "pipeline_control_flow.json")
    lineage = build_pipeline_lineage(pipeline)

    # Copy nested inside the ForEach loop body is visible in pipeline lineage.
    (loop_node,) = lineage.graph.find("StageRegion.region_id")
    assert lineage.graph.node(loop_node).column == "region_id"

    # Copy nested two levels deep (ForEach -> Switch -> case) is also visible.
    (gold_node,) = lineage.graph.find("GoldTierSink.region_id")
    assert lineage.graph.node(gold_node).stream == "GoldTierSink"
    assert lineage.graph.edge(loop_node, gold_node).transformation == "CopyGoldTier"
