import json

from app.parsers.dataflow_parser import DataFlowParser
from app.parsers.pipeline_parser import PipelineParser
from app.services.inventory import Inventory, build_inventory


def _pipeline(read_fixture_bytes, name):
    p, _ = PipelineParser().parse(json.loads(read_fixture_bytes(name)))
    return p


def _data_flow(read_fixture_bytes, name):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes(name)))
    return df


def test_empty_inventory():
    inv = build_inventory([], [])
    assert isinstance(inv, Inventory)
    assert inv.is_empty


def test_inventory_across_pipeline_and_data_flow(read_fixture_bytes):
    pipeline = _pipeline(read_fixture_bytes, "pipeline_copy_translator.json")
    data_flow = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    inv = build_inventory([pipeline], [data_flow])
    assert not inv.is_empty

    # sources: the pipeline Copy input + both data-flow sources
    src_names = {(s.artifact, s.name) for s in inv.sources}
    assert ("Data flow: CustomerEnrichFlow", "CustomerSource") in src_names
    assert ("Pipeline: StageCustomerPipeline / CopyRawToStage", "RawCustomer") in src_names
    stage_source = next(s for s in inv.sources if s.name == "CustomerSource")
    assert stage_source.dataset == "StageCustomer"
    assert stage_source.column_count == 5

    # sinks
    sink_names = {s.name for s in inv.sinks}
    assert {"StageCustomer", "CustomerSink"} <= sink_names

    # transformations: data-flow operators + every pipeline activity
    tr = {(t.artifact, t.name, t.kind) for t in inv.transformations}
    assert ("Data flow: CustomerEnrichFlow", "JoinRegion", "join") in tr
    assert ("Pipeline: StageCustomerPipeline", "RunCustomerFlow", "ExecuteDataFlow") in tr


def test_dataset_entry_ties_reader_and_writer(read_fixture_bytes):
    pipeline = _pipeline(read_fixture_bytes, "pipeline_copy_translator.json")
    data_flow = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    inv = build_inventory([pipeline], [data_flow])

    stage = next(d for d in inv.datasets if d.name == "StageCustomer")
    assert "Pipeline: StageCustomerPipeline" in stage.written_by
    assert "Data flow: CustomerEnrichFlow" in stage.read_by
    assert stage.column_count == 5

    raw = next(d for d in inv.datasets if d.name == "RawCustomer")
    assert raw.written_by == ()  # never produced by an uploaded artifact
    dim = next(d for d in inv.datasets if d.name == "DimCustomer")
    assert dim.read_by == ()  # terminal


def test_unrecognized_flags_are_carried(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_unknown_op.json")
    inv = build_inventory([], [df])
    magic = next(t for t in inv.transformations if t.name == "DoMagic")
    assert magic.recognized is False


def test_inventory_is_deterministic(read_fixture_bytes):
    args = (
        [_pipeline(read_fixture_bytes, "pipeline_multi_activity.json")],
        [_data_flow(read_fixture_bytes, "dataflow_customer_full.json")],
    )
    assert build_inventory(*args) == build_inventory(*args)
