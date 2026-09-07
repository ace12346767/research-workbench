from __future__ import annotations

import asyncio

import httpx

from agent_workbench.core.models import ToolCall
from agent_workbench.providers.mock import MockProvider
from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider, OpenAIStreamDecoder, parse_stream_payload


def test_parse_stream_payload_normalizes_reasoning_and_content() -> None:
    chunks = parse_stream_payload(
        {
            "choices": [
                {
                    "delta": {"reasoning_content": "thinking", "content": "answer"},
                    "finish_reason": None,
                }
            ]
        }
    )
    assert [(chunk.type, chunk.delta) for chunk in chunks] == [
        ("reasoning", "thinking"),
        ("content", "answer"),
    ]


def test_parse_stream_payload_normalizes_complete_tool_call() -> None:
    chunks = parse_stream_payload(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {"name": "echo", "arguments": '{"text":"hi"}'},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    assert chunks[0].tool_call == ToolCall(call_id="call-1", name="echo", arguments={"text": "hi"})
    assert chunks[-1].finish_reason == "tool_calls"


def test_mock_provider_supports_direct_answer() -> None:
    async def collect():
        provider = MockProvider()
        return [
            chunk
            async for chunk in provider.stream(
                [{"role": "user", "content": "hello"}],
                [],
                asyncio.Event(),
            )
        ]

    chunks = asyncio.run(collect())
    assert [chunk.type for chunk in chunks] == ["reasoning", "content", "finish"]
    assert "hello" in chunks[1].delta


def test_stream_decoder_assembles_split_tool_call_before_finish() -> None:
    decoder = OpenAIStreamDecoder()

    first = decoder.feed(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "read_", "arguments": "{\"pa"}}]}, "finish_reason": None}]}
    )
    second = decoder.feed(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": "th\":\"main.py\"}"}}]}, "finish_reason": "tool_calls"}]}
    )

    assert first == []
    assert [chunk.type for chunk in second] == ["tool_call", "finish"]
    assert second[0].tool_call == ToolCall(call_id="call-1", name="read_file", arguments={"path": "main.py"})


def test_mock_provider_supports_edit_and_card_demo_commands() -> None:
    provider = MockProvider()

    edit = provider._command("/edit main.py | answer = 1 | answer = 2")
    card = provider._command("/save Routing note | Use abstain | router, design")

    assert edit == ToolCall(
        call_id=edit.call_id,
        name="edit_file",
        arguments={"path": "main.py", "expected_text": "answer = 1", "replacement_text": "answer = 2"},
    )
    assert card.name == "save_card"
    assert card.arguments == {"title": "Routing note", "content": "Use abstain", "tags": ["router", "design"]}


def test_provider_lists_models_and_tests_connection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]})

    provider = OpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="secret",
        model="model-a",
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(provider.list_models()) == ["model-a", "model-b"]
    assert asyncio.run(provider.test_connection()) == {"ok": True, "model_visible": True}


def test_provider_retries_transient_stream_failure_before_emitting_content() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporarily unavailable")
        body = 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n' \
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n' \
            'data: [DONE]\n\n'
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})

    provider = OpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="secret",
        model="model-a",
        retry_count=1,
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    async def collect():
        return [chunk async for chunk in provider.stream([{"role": "user", "content": "hi"}], [], asyncio.Event())]

    chunks = asyncio.run(collect())
    assert attempts == 2
    assert [chunk.type for chunk in chunks] == ["content", "finish"]
