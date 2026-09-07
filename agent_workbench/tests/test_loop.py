from __future__ import annotations

import asyncio
from pathlib import Path

from agent_workbench.core.loop import AgentLoop
from agent_workbench.core.models import ProviderChunk, ToolCall
from agent_workbench.core.workspace import Workspace
from agent_workbench.knowledge.cards import CardApprovalRequired, CardManager
from agent_workbench.tools.registry import ToolRegistry


class ScriptedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, tools, cancel_event):
        self.calls += 1
        if self.calls == 1:
            yield ProviderChunk(
                type="tool_call",
                tool_call=ToolCall(call_id="call-1", name="echo", arguments={"text": "hi"}),
            )
            yield ProviderChunk(type="finish", finish_reason="tool_calls")
        else:
            yield ProviderChunk(type="content", delta="result: hi")
            yield ProviderChunk(type="finish", finish_reason="stop")


def test_agent_loop_executes_tool_and_emits_ordered_events() -> None:
    registry = ToolRegistry()
    registry.register("echo", "Echo text", lambda text: {"text": text})
    loop = AgentLoop(provider=ScriptedProvider(), tools=registry, max_iterations=3)

    events = asyncio.run(loop.run("req-1", "say hi"))
    types = [event.type for event in events]

    assert types == ["tool_call", "tool_result", "answer", "done"]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert events[-1].data["finish_reason"] == "stop"


class EditProvider:
    async def stream(self, messages, tools, cancel_event):
        yield ProviderChunk(
            type="tool_call",
            tool_call=ToolCall(
                call_id="edit-1",
                name="edit_file",
                arguments={"path": "main.py", "expected_text": "answer = 1", "replacement_text": "answer = 2"},
            ),
        )
        yield ProviderChunk(type="finish", finish_reason="tool_calls")


def test_agent_loop_emits_approval_and_pauses_write(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("answer = 1\n", encoding="utf-8")
    workspace = Workspace(tmp_path)
    registry = ToolRegistry()
    registry.register("edit_file", "Edit with approval", workspace.propose_edit)
    loop = AgentLoop(provider=EditProvider(), tools=registry, max_iterations=2)

    events = asyncio.run(loop.run("req-edit", "change answer"))

    assert [event.type for event in events] == ["tool_call", "approval", "done"]
    assert events[1].data["action"] == "edit_file"
    assert "-answer = 1" in events[1].data["diff"]
    assert events[-1].data["finish_reason"] == "waiting_approval"
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "answer = 1\n"


def test_agent_loop_observes_external_cancellation() -> None:
    class WaitingProvider:
        async def stream(self, messages, tools, cancel_event):
            while not cancel_event.is_set():
                await asyncio.sleep(0.005)
            yield ProviderChunk(type="finish", finish_reason="cancelled")

    async def scenario() -> list:
        cancel_event = asyncio.Event()
        loop = AgentLoop(provider=WaitingProvider(), tools=ToolRegistry())
        task = asyncio.create_task(loop.run("req-cancel", "wait", cancel_event=cancel_event))
        await asyncio.sleep(0.02)
        cancel_event.set()
        return await task

    events = asyncio.run(scenario())
    assert events[-1].type == "done"
    assert events[-1].data["finish_reason"] == "cancelled"


def test_agent_loop_emits_card_approval(tmp_path: Path) -> None:
    class CardProvider:
        async def stream(self, messages, tools, cancel_event):
            yield ProviderChunk(
                type="tool_call",
                tool_call=ToolCall(
                    call_id="card-1",
                    name="save_card",
                    arguments={"title": "Routing", "content": "Use abstain", "tags": ["router"]},
                ),
            )
            yield ProviderChunk(type="finish", finish_reason="tool_calls")

    manager = CardManager(tmp_path / "cards")
    registry = ToolRegistry()

    def save_card(title: str, content: str, tags: list[str] | None = None) -> None:
        raise CardApprovalRequired(manager.propose(title, content, tags))

    registry.register("save_card", "Save a card after approval", save_card)
    events = asyncio.run(AgentLoop(provider=CardProvider(), tools=registry).run("req-card", "save it"))

    assert [event.type for event in events] == ["tool_call", "approval", "done"]
    assert events[1].data["action"] == "save_card"
    assert events[1].data["title"] == "Routing"


def test_agent_loop_supplies_stable_system_tool_guidance() -> None:
    class CaptureProvider:
        def __init__(self) -> None:
            self.messages = None

        async def stream(self, messages, tools, cancel_event):
            self.messages = messages
            yield ProviderChunk(type="finish", finish_reason="stop")

    provider = CaptureProvider()
    asyncio.run(AgentLoop(provider=provider, tools=ToolRegistry()).run("req-guide", "hello"))

    assert provider.messages[0]["role"] == "system"
    assert "untrusted evidence" in provider.messages[0]["content"]
    assert provider.messages[1] == {"role": "user", "content": "hello"}


def test_agent_loop_reports_usage_and_duration_in_done_event() -> None:
    class UsageProvider:
        async def stream(self, messages, tools, cancel_event):
            yield ProviderChunk(type="content", delta="ok")
            yield ProviderChunk(type="usage", usage={"prompt_tokens": 4, "completion_tokens": 1})
            yield ProviderChunk(type="finish", finish_reason="stop")

    events = asyncio.run(AgentLoop(provider=UsageProvider(), tools=ToolRegistry()).run("req-usage", "hello"))
    done = events[-1]

    assert done.type == "done"
    assert done.data["usage"] == {"prompt_tokens": 4, "completion_tokens": 1, 'complete': True, 'requests': 1}
    assert isinstance(done.data["duration_ms"], int)
