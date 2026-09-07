import asyncio
import json

import pytest

from agent_workbench.config import AppConfig
from agent_workbench.core.models import ProviderChunk, RouteDecision, ToolCall
from agent_workbench.providers.mock import MockProvider
from agent_workbench.runtime import ApplicationRuntime
from agent_workbench.server.api import create_app
from fastapi.testclient import TestClient


class Router:
    async def route(self, message):
        return RouteDecision(effort=None, source="abstain", reason="test")


async def embed(texts):
    return [[1.0, 0.0] for _ in texts]


def make_runtime(tmp_path, provider=None):
    runtime = ApplicationRuntime(data_dir=tmp_path / "data", router=Router(),
                                 provider=provider or MockProvider(), embed=embed)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("old", encoding="utf-8")
    runtime.set_workspace(workspace)
    return runtime


async def until_approval(stream):
    events = []
    async for event in stream:
        events.append(event)
        if event.type == "approval":
            return events, event.data["approval_id"]
    pytest.fail("expected an approval")


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_decision_resumes_same_turn_and_fills_tool_result(tmp_path, decision):
    async def scenario():
        runtime = make_runtime(tmp_path)
        stream = runtime.chat_stream("edit", "/edit a.txt | old | new")
        events, key = await until_approval(stream)
        if decision == "approve":
            result = await runtime.approve_and_index(key)
            repeated = await runtime.approve_and_index(key)
            assert repeated["status"] == result["status"] == "applied"
        else:
            runtime.reject(key)
        events.extend([event async for event in stream])
        assert events[-1].data["finish_reason"] == "stop"
        assert any(event.type == "answer" for event in events)
        tool = next(event for event in events if event.type == "tool_result")
        assert tool.data["output"]["status"] == ("applied" if decision == "approve" else "rejected")
        assert "original_text" not in tool.data["output"]
        assert "updated_text" not in tool.data["output"]
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert runtime.workspace._read_text("a.txt") == ("new" if decision == "approve" else "old")
        assert not runtime.active_cancellations
    asyncio.run(scenario())


def test_cancel_pending_approval_prevents_late_write(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        stream = runtime.chat_stream("edit", "/edit a.txt | old | new")
        _, key = await until_approval(stream)
        assert runtime.cancel("edit")
        with pytest.raises(ValueError):
            await runtime.approve_and_index(key)
        events = [event async for event in stream]
        assert events[-1].data["finish_reason"] == "cancelled"
        assert runtime.workspace._read_text("a.txt") == "old"
    asyncio.run(scenario())


def test_expired_approval_terminates_without_write(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        runtime.approval_timeout_seconds = 0.01
        stream = runtime.chat_stream("edit", "/edit a.txt | old | new")
        _, key = await until_approval(stream)
        events = await asyncio.wait_for(_collect(stream), 1)
        assert events[-1].data["finish_reason"] == "approval_expired"
        with pytest.raises(ValueError):
            await runtime.approve_and_index(key)
        assert runtime.workspace._read_text("a.txt") == "old"
    asyncio.run(scenario())


async def _collect(stream):
    return [event async for event in stream]


def test_disconnect_invalidates_pending_approval(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        stream = runtime.chat_stream("edit", "/edit a.txt | old | new")
        _, key = await until_approval(stream)
        await stream.aclose()
        with pytest.raises(ValueError):
            await runtime.approve_and_index(key)
        assert not runtime.active_cancellations
        assert runtime.workspace._read_text("a.txt") == "old"
    asyncio.run(scenario())


def test_active_turn_prevents_workspace_and_provider_switch(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        stream = runtime.chat_stream("edit", "/edit a.txt | old | new")
        await until_approval(stream)
        try:
            with pytest.raises(ValueError):
                runtime.set_workspace(tmp_path)
            with pytest.raises(ValueError):
                runtime.update_settings(AppConfig(provider_mode="mock"))
            with pytest.raises(ValueError):
                await runtime.chat("edit", "duplicate")
        finally:
            await stream.aclose()
    asyncio.run(scenario())


def test_multiple_tools_continue_after_approval(tmp_path):
    class Provider:
        async def stream(self, messages, tools, cancel_event):
            if messages[-1]["role"] == "user":
                for call in [
                    ToolCall(call_id="write", name="edit_file", arguments={"path": "a.txt", "expected_text": "old", "replacement_text": "new"}),
                    ToolCall(call_id="read", name="read_file", arguments={"path": "a.txt"}),
                ]:
                    yield ProviderChunk(type="tool_call", tool_call=call)
            else:
                results = [item for item in messages if item["role"] == "tool"]
                assert [item["tool_call_id"] for item in results] == ["write", "read"]
                assert results[1]["content"] == "new"
                yield ProviderChunk(type="content", delta="both tools finished")
            yield ProviderChunk(type="finish", finish_reason="stop")
    async def scenario():
        runtime = make_runtime(tmp_path, Provider())
        stream = runtime.chat_stream("edit", "edit then read")
        _, key = await until_approval(stream)
        await runtime.approve_and_index(key)
        events = [event async for event in stream]
        assert [event.data["call_id"] for event in events if event.type == "tool_result"] == ["write", "read"]
        assert events[-1].data["finish_reason"] == "stop"
    asyncio.run(scenario())


def test_sse_error_has_monotonic_terminal_event(tmp_path):
    class BrokenProvider:
        async def stream(self, *args):
            yield ProviderChunk(type="content", delta="partial")
            raise RuntimeError("upstream broke")
    runtime = make_runtime(tmp_path, BrokenProvider())
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/chat/stream", json={"message": "hello"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in events] == ["route", "context", "answer", "error", "done"]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
    assert events[-1]["data"]["finish_reason"] == "error"


def test_router_failure_abstains_and_does_not_abort_chat(tmp_path):
    class BrokenRouter:
        async def route(self, message):
            raise RuntimeError("router offline")
    runtime = make_runtime(tmp_path)
    runtime.router = BrokenRouter()
    events = asyncio.run(runtime.chat("route", "hello"))
    assert events[0].data["source"] == "abstain"
    assert events[0].data["reason"] == "router_error"
    assert events[-1].data["finish_reason"] == "stop"


def test_card_index_failure_reports_saved_state_and_resumes(tmp_path):
    async def unavailable(texts):
        raise RuntimeError("embedding unavailable")
    async def scenario():
        runtime = make_runtime(tmp_path)
        runtime.knowledge.embed = unavailable
        stream = runtime.chat_stream("card", "/save Note | useful text | test")
        _, key = await until_approval(stream)
        result = await runtime.approve_and_index(key)
        events = [event async for event in stream]
        assert result["status"] == "applied"
        assert result["index_status"] == "failed"
        assert runtime.cards.list_cards()[0].content == "useful text"
        assert events[-1].data["finish_reason"] == "stop"
        tool = next(event for event in events if event.type == "tool_result")
        assert tool.data["output"]["index_status"] == "failed"
    asyncio.run(scenario())
