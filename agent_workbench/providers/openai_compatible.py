from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncIterator

import httpx

from agent_workbench.core.models import ProviderChunk, ToolCall
from agent_workbench.core.conversation import ProviderContextOverflow
from agent_workbench.providers.reasoning import model_profile


def parse_stream_payload(payload: dict[str, Any]) -> list[ProviderChunk]:
    chunks: list[ProviderChunk] = []
    choices = payload.get("choices") or []
    if not choices:
        if payload.get("usage"):
            chunks.append(ProviderChunk(type="usage", usage=payload["usage"]))
        return chunks
    choice = choices[0]
    delta = choice.get("delta") or {}
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning:
        chunks.append(ProviderChunk(type="reasoning", delta=str(reasoning)))
    if delta.get("content"):
        chunks.append(ProviderChunk(type="content", delta=str(delta["content"])))
    for raw_call in delta.get("tool_calls") or []:
        function = raw_call.get("function") or {}
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            continue
        if raw_call.get("id") and function.get("name"):
            chunks.append(
                ProviderChunk(
                    type="tool_call",
                    tool_call=ToolCall(
                        call_id=str(raw_call["id"]),
                        name=str(function["name"]),
                        arguments=arguments,
                    ),
                )
            )
    if payload.get("usage"):
        chunks.append(ProviderChunk(type="usage", usage=payload["usage"]))
    if choice.get("finish_reason") is not None:
        chunks.append(ProviderChunk(type="finish", finish_reason=str(choice["finish_reason"])))
    return chunks


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_output_tokens: int | None = None,
        retry_count: int = 2,
        retry_backoff_seconds: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.retry_count = max(0, retry_count)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.transport = transport
        profile = model_profile(model)
        self.reasoning_effort = reasoning_effort if reasoning_effort in profile.levels else None
        self.preserve_reasoning = profile.preserve_reasoning

    def _client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def list_models(self) -> list[str]:
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/models", headers=self._headers)
            response.raise_for_status()
        return sorted(
            str(item["id"])
            for item in response.json().get("data", [])
            if isinstance(item, dict) and item.get("id")
        )

    async def test_connection(self) -> dict[str, bool]:
        models = await self.list_models()
        return {"ok": True, "model_visible": self.model in models}

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{k:v for k,v in m.items() if not k.startswith('_')} for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        if self.max_output_tokens is not None:
            payload['max_completion_tokens' if model_profile(self.model).completion_tokens else 'max_tokens'] = self.max_output_tokens
        profile = model_profile(self.model)
        if self.reasoning_effort in profile.levels:
            payload['reasoning_effort'] = self.reasoning_effort
            if profile.explicit_thinking:
                payload['thinking'] = {'type': 'enabled'}

        emitted = False
        for attempt in range(self.retry_count + 1):
            decoder = OpenAIStreamDecoder()
            try:
                async with self._client() as client:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        headers=self._headers,
                        json=payload,
                    ) as response:
                        if response.status_code in {400, 413}:
                            await response.aread()
                            try:
                                error = response.json().get('error', {})
                                code = error.get('code') if isinstance(error, dict) else None
                            except (ValueError, AttributeError):
                                code = None
                            if code in {'context_length_exceeded', 'context_window_exceeded'}:
                                raise ProviderContextOverflow('Provider rejected the context size')
                            if any(isinstance(m.get('content'),list) and any(p.get('type')=='image_url' for p in m['content']) for m in messages):
                                raise ValueError('包含图片的请求被接入端拒绝；请核对模型的图片支持与请求配置，附件已保留')
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if cancel_event.is_set():
                                yield ProviderChunk(type="finish", finish_reason="cancelled")
                                return
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            for chunk in decoder.feed(json.loads(data)):
                                emitted = True
                                yield chunk
                        return
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                transient = status is None or status in {408, 409, 425, 429} or (status is not None and status >= 500)
                if emitted or not transient or attempt >= self.retry_count:
                    raise
                if self.retry_backoff_seconds:
                    await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))


class OpenAIStreamDecoder:
    def __init__(self) -> None:
        self.tool_parts: dict[int, dict[str, str]] = defaultdict(
            lambda: {"id": "", "name": "", "arguments": ""}
        )

    def feed(self, payload: dict[str, Any]) -> list[ProviderChunk]:
        choices = payload.get("choices") or []
        if not choices:
            return parse_stream_payload(payload)
        choice = choices[0]
        delta = choice.get("delta") or {}
        for call in delta.get("tool_calls") or []:
            part = self.tool_parts[int(call.get("index", 0))]
            if call.get("id"):
                part["id"] = str(call["id"])
            function = call.get("function") or {}
            if function.get("name"):
                part["name"] += str(function["name"])
            if function.get("arguments"):
                part["arguments"] += str(function["arguments"])

        finish_reason = choice.get("finish_reason")
        clean_choice = {**choice, "delta": {**delta, "tool_calls": []}}
        if finish_reason == "tool_calls":
            clean_choice["finish_reason"] = None
        chunks = parse_stream_payload({**payload, "choices": [clean_choice]})
        if finish_reason == "tool_calls":
            for index in sorted(self.tool_parts):
                part = self.tool_parts[index]
                if not part["id"] or not part["name"]:
                    raise ValueError("incomplete streamed tool call")
                chunks.append(
                    ProviderChunk(
                        type="tool_call",
                        tool_call=ToolCall(
                            call_id=part["id"],
                            name=part["name"],
                            arguments=json.loads(part["arguments"] or "{}"),
                        ),
                    )
                )
            self.tool_parts.clear()
            chunks.append(ProviderChunk(type="finish", finish_reason="tool_calls"))
        return chunks
