from __future__ import annotations

import asyncio

from agent_workbench.core.loop import AgentLoop
from agent_workbench.core.models import RouteDecision
from agent_workbench.providers.mock import MockProvider
from agent_workbench.server.chat_service import ChatService
from agent_workbench.tools.registry import ToolRegistry


class StubRouter:
    async def route(self, text: str) -> RouteDecision:
        return RouteDecision(effort="standard", source="semantic", score=0.8, margin=0.1, reason="semantic_match")


def test_chat_service_emits_route_before_model_events() -> None:
    loop = AgentLoop(provider=MockProvider(), tools=ToolRegistry())
    service = ChatService(router=StubRouter(), loop=loop)

    events = asyncio.run(service.run("req-1", "hello"))

    assert events[0].type == "route"
    assert events[0].data["effort"] == "standard"
    assert events[1].sequence == 2
    assert events[-1].type == "done"
    answer = "".join(event.data.get("delta", "") for event in events if event.type == "answer")
    assert "Internal effort guidance" not in answer


def test_chat_service_stream_yields_before_provider_finishes() -> None:
    first_chunk_seen = asyncio.Event()
    release_finish = asyncio.Event()

    class SlowLoop:
        async def stream(self, request_id, message, cancel_event):
            from agent_workbench.core.models import StreamEvent

            first_chunk_seen.set()
            yield StreamEvent(request_id=request_id, sequence=1, type="answer", data={"delta": "first"})
            await release_finish.wait()
            yield StreamEvent(request_id=request_id, sequence=2, type="done", data={"finish_reason": "stop"})

    async def scenario() -> None:
        service = ChatService(router=StubRouter(), loop=SlowLoop())
        iterator = service.stream("req-stream", "hello", asyncio.Event())
        route = await anext(iterator)
        answer = await anext(iterator)
        assert route.type == "route"
        assert answer.data["delta"] == "first"
        assert first_chunk_seen.is_set()
        release_finish.set()
        done = await anext(iterator)
        assert done.type == "done"

    asyncio.run(scenario())
