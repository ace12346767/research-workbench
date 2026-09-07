from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from agent_workbench.core.models import RouteDecision, StreamEvent
from agent_workbench.config import AppConfig
from agent_workbench.providers.reasoning import effective_reasoning, normalize_config, reasoning_capabilities
from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider


class ChatService:
    def __init__(self, *, router: Any, loop: Any, config: AppConfig | None = None) -> None:
        self.router = router
        self.loop = loop
        self.config = normalize_config(config or AppConfig())

    async def stream(
        self,
        request_id: str,
        message: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        cancel_event = cancel_event or asyncio.Event()
        try:
            route = await self.router.route(message) if self.config.guidance.enabled else RouteDecision(
                effort=None, source='abstain', reason='disabled')
        except Exception:
            route = RouteDecision(effort=None, source="abstain", reason="router_error", router_state="unavailable")
        prompt = getattr(self.config.guidance.profiles, route.effort).prompt if route.effort else None
        self.loop.guidance_prompt = prompt
        actual_reasoning = effective_reasoning(self.config, route.effort)
        if isinstance(getattr(self.loop, 'provider', None), OpenAICompatibleProvider):
            # Freeze this turn's policy without mutating the shared provider or draft.
            self.loop.provider = copy.copy(self.loop.provider)
            self.loop.provider.reasoning_effort = actual_reasoning
        yield StreamEvent(
            request_id=request_id,
            sequence=1,
            type="route",
            data={**route.model_dump(), 'guidance_prompt': prompt,
                  'reasoning_effort': actual_reasoning,
                  'reasoning_mode': 'auto' if self.config.guidance.link_reasoning else 'manual',
                  'reasoning_policy': reasoning_capabilities(self.config)['policy']},
        )
        if cancel_event.is_set():
            yield StreamEvent(
                request_id=request_id,
                sequence=2,
                type="done",
                data={"finish_reason": "cancelled"},
            )
            return
        async with aclosing(self.loop.stream(request_id, message, cancel_event)) as stream:
            async for loop_event in stream:
                yield loop_event.model_copy(update={"sequence": loop_event.sequence + 1})

    async def run(
        self,
        request_id: str,
        message: str,
        cancel_event: asyncio.Event | None = None,
    ) -> list[StreamEvent]:
        return [event async for event in self.stream(request_id, message, cancel_event)]
