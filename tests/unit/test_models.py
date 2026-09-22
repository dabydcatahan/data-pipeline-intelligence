import pytest
from app.models.activity import Activity, ActivityDependency
from app.models.artifact import ArtifactKind
from app.models.common import DatasetReference
from app.models.pipeline import Pipeline
from pydantic import ValidationError


def _wait(name, deps=()):
    return Activity(
        name=name,
        type="Wait",
        depends_on=tuple(ActivityDependency(activity=d) for d in deps),
    )


def test_models_are_frozen():
    ref = DatasetReference(name="X")
    with pytest.raises(ValidationError):
        ref.name = "Y"


def test_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        DatasetReference(name="X", bogus=1)


def test_pipeline_activity_type_counts():
    p = Pipeline(
        name="P",
        activities=(
            Activity(name="c1", type="Copy"),
            Activity(name="c2", type="Copy"),
            _wait("w1"),
        ),
    )
    assert p.activity_types == {"Copy": 2, "Wait": 1}
    assert p.activity("w1").type == "Wait"
    assert p.activity("missing") is None


def test_execution_order_branching():
    p = Pipeline(
        name="P",
        activities=(
            _wait("root"),
            _wait("a", ["root"]),
            _wait("b", ["root"]),
            _wait("join", ["a", "b"]),
        ),
    )
    order = p.execution_order().order
    assert order[0] == "root"
    assert order[-1] == "join"
    assert order.index("a") < order.index("join")


def test_execute_data_flow_flag():
    a = Activity(name="df", type="ExecuteDataFlow", data_flow_reference="MyFlow")
    assert a.executes_data_flow is True
    assert a.data_flow_reference == "MyFlow"


def test_artifact_kind_labels():
    assert ArtifactKind.PIPELINE.label == "Pipeline"
    assert ArtifactKind.MAPPING_DATA_FLOW.label == "Mapping Data Flow"
    assert ArtifactKind.UNKNOWN.label.startswith("Unknown")
