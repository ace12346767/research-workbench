from agent_workbench.core.models import StreamEvent


def test_stream_event_serializes_stable_envelope() -> None:
    event = StreamEvent(
        request_id="req-1",
        sequence=3,
        type="answer",
        data={"delta": "hello"},
    )
    assert event.model_dump() == {
        "request_id": "req-1",
        "sequence": 3,
        "type": "answer",
        "data": {"delta": "hello"},
    }
