import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from agent_workbench.config import AppConfig
from agent_workbench.core.models import ProviderChunk, ToolCall
from agent_workbench.server.api import create_app
from agent_workbench.tests.test_approval_flow import make_runtime


class CaptureProvider:
    def __init__(self):
        self.requests = []

    async def stream(self, messages, tools, cancel_event):
        self.requests.append(copy.deepcopy(messages))
        yield ProviderChunk(type="content", delta="Remembered response")
        yield ProviderChunk(type="finish", finish_reason="stop")


def test_followup_receives_prior_complete_turn(tmp_path):
    provider = CaptureProvider()
    runtime = make_runtime(tmp_path, provider)
    asyncio.run(runtime.chat("first", "My project is AWB"))
    asyncio.run(runtime.chat("second", "What is my project?"))
    messages = provider.requests[-1]
    assert [item["role"] for item in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "My project is AWB"
    assert messages[2]["content"] == "Remembered response"


def test_conversation_api_refresh_and_clear_are_synchronized(tmp_path):
    provider = CaptureProvider()
    runtime = make_runtime(tmp_path, provider)
    with TestClient(create_app(runtime)) as client:
        client.post("/api/chat/stream", json={"message": "remember this"})
        snapshot = client.get("/api/conversation")
        assert snapshot.status_code == 200
        assert snapshot.json()["turns"][0]["user"] == "remember this"
        assert snapshot.json()["turns"][0]["assistant"] == "Remembered response"
        assert client.delete("/api/conversation").status_code == 200
        assert client.get("/api/conversation").json()["turns"] == []
        client.post("/api/chat/stream", json={"message": "new task"})
        assert [item["role"] for item in provider.requests[-1]] == ["system", "user"]


def test_history_keeps_all_complete_turns(tmp_path):
    provider = CaptureProvider()
    runtime = make_runtime(tmp_path, provider)
    async def scenario():
        for index in range(10):
            await runtime.chat(str(index), f"message-{index}")
    asyncio.run(scenario())
    users = [item["content"] for item in provider.requests[-1] if item["role"] == "user"]
    assert users == [f"message-{index}" for index in range(10)]
    assert len(runtime.conversation_snapshot()["turns"]) == 10


def test_workspace_switch_clears_history(tmp_path):
    runtime = make_runtime(tmp_path, CaptureProvider())
    asyncio.run(runtime.chat("old", "workspace information"))
    runtime.set_workspace(tmp_path)
    assert runtime.conversation_snapshot()["turns"] == []


def test_cancelled_partial_response_is_saved_but_not_sent_as_complete_context(tmp_path):
    class CancelProvider:
        async def stream(self, messages, tools, cancel_event):
            yield ProviderChunk(type="content", delta="partial")
            cancel_event.set()
            yield ProviderChunk(type="finish", finish_reason="cancelled")
    runtime = make_runtime(tmp_path, CancelProvider())
    asyncio.run(runtime.chat("cancel", "do not retain"))
    assert runtime.conversation_snapshot()['turns'][0]['status'] == 'cancelled'
    assert runtime.conversation.turns == []


def test_overlong_user_message_is_rejected_before_provider(tmp_path):
    provider = CaptureProvider()
    runtime = make_runtime(tmp_path, provider)
    runtime.config = AppConfig(context_window=4096, max_output_tokens=256)
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/chat/stream", json={"message": "x" * 10000})
        assert '"code": "context_limit"' in response.text
        assert '"finish_reason": "error"' in response.text
    assert provider.requests == []


def test_budget_compacts_whole_history_turns_with_notice(tmp_path):
    provider = CaptureProvider()
    runtime = make_runtime(tmp_path, provider)
    asyncio.run(runtime.chat("large", "old " * 2000))
    runtime.config = AppConfig(context_window=6144, max_output_tokens=256)
    events = asyncio.run(runtime.chat("small", "new question"))
    assert any(event.type == "context" and event.data["compacted_turns"] == 1 and event.data['summary_kind'] == 'semantic' for event in events)
    assert provider.requests[-1][-1]['content'] == 'new question'
    assert len(runtime.conversation_snapshot()['turns']) == 2


def test_budget_limits_tool_text_without_breaking_call_pairing(tmp_path):
    class ToolProvider(CaptureProvider):
        async def stream(self, messages, tools, cancel_event):
            self.requests.append(copy.deepcopy(messages))
            if messages[-1]["role"] == "user":
                yield ProviderChunk(type="tool_call", tool_call=ToolCall(
                    call_id="read", name="read_file", arguments={"path": "large.txt"}))
            else:
                yield ProviderChunk(type="content", delta="read completed")
            yield ProviderChunk(type="finish", finish_reason="stop")
    provider = ToolProvider()
    runtime = make_runtime(tmp_path, provider)
    (runtime.workspace.root / "large.txt").write_text("x" * 30000)
    runtime.config = AppConfig(context_window=8192, max_output_tokens=256)
    events = asyncio.run(runtime.chat("tool", "read file"))
    request = provider.requests[-1]
    result = request[-1]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == request[-2]["tool_calls"][0]["id"]
    assert "[TRUNCATED" in result["content"]
    assert len(json.dumps(request).encode()) < 8192
    assert any(event.type == "context" and event.data["truncated_tools"] for event in events)


def test_history_preserves_tool_results_for_followup(tmp_path):
    runtime = make_runtime(tmp_path)
    asyncio.run(runtime.chat("read", "/read a.txt"))
    provider = CaptureProvider()
    runtime.provider = provider
    asyncio.run(runtime.chat("followup", "what did that file contain?"))
    tools = [item for item in provider.requests[-1] if item["role"] == "tool"]
    assert tools[0]["content"] == "old"


def test_clear_during_active_request_is_rejected(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        stream = runtime.chat_stream("active", "hello")
        await anext(stream)
        try:
            with pytest.raises(ValueError):
                runtime.clear_conversation()
        finally:
            await stream.aclose()
    asyncio.run(scenario())
