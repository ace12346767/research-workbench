from __future__ import annotations

import asyncio
import shlex
import uuid
from typing import Any, AsyncIterator

from agent_workbench.core.models import ProviderChunk, ToolCall


class MockProvider:
    """Deterministic provider for demos and tests that need no API key."""

    supports_semantic_compaction = False

    async def list_models(self) -> list[str]:
        return ["mock-model"]

    async def test_connection(self) -> dict[str, bool]:
        return {"ok": True, "model_visible": True}

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ProviderChunk]:
        if cancel_event.is_set():
            yield ProviderChunk(type="finish", finish_reason="cancelled")
            return

        last = messages[-1]
        if last.get("role") == "tool":
            yield ProviderChunk(type="content", delta=f"工具执行结果：{last.get('content', '')}")
            yield ProviderChunk(type="finish", finish_reason="stop")
            return

        content = last.get('content') or ''
        text = '\n'.join(p.get('text','') for p in content if p.get('type')=='text') if isinstance(content,list) else str(content)
        if isinstance(content,list) and any(p.get('type')=='image_url' for p in content):
            yield ProviderChunk(type='content',delta='离线演示已收到图片附件，但不具备视觉理解能力。')
            yield ProviderChunk(type='finish',finish_reason='stop')
            return
        text = text.split("\n\n[Internal effort guidance:", 1)[0]
        command = self._command(text)
        if command is not None:
            yield ProviderChunk(type="reasoning", delta="正在准备受控工具调用。")
            yield ProviderChunk(type="tool_call", tool_call=command)
            yield ProviderChunk(type="finish", finish_reason="tool_calls")
            return

        yield ProviderChunk(type="reasoning", delta="离线演示正在组织直接回答。")
        yield ProviderChunk(type="content", delta=f"演示回答：{text}")
        yield ProviderChunk(type="finish", finish_reason="stop")

    def _command(self, text: str) -> ToolCall | None:
        raw_parts = [part.strip() for part in text.split("|")]
        command_text = raw_parts[0]
        try:
            parts = shlex.split(command_text, posix=False)
        except ValueError:
            return None
        if not parts:
            return None
        name = parts[0].casefold()
        arguments: dict[str, Any]
        if name == "/list":
            arguments = {"directory": parts[1] if len(parts) > 1 else "."}
            tool_name = "list_files"
        elif name == "/read" and len(parts) > 1:
            arguments = {"path": parts[1]}
            tool_name = "read_file"
        elif name == "/grep" and len(parts) > 1:
            arguments = {"query": " ".join(parts[1:]), "path": "."}
            tool_name = "grep_search"
        elif name == "/kb" and len(parts) > 1:
            arguments = {"query": " ".join(parts[1:])}
            tool_name = "ask_kb"
        elif name == "/edit" and len(parts) > 1 and len(raw_parts) == 3:
            arguments = {
                "path": parts[1],
                "expected_text": raw_parts[1],
                "replacement_text": raw_parts[2],
            }
            tool_name = "edit_file"
        elif name == '/draft' and len(raw_parts) >= 3:
            arguments = {'title': command_text.removeprefix(parts[0]).strip(), 'content': raw_parts[1], 'reason': raw_parts[2]}
            tool_name = 'draft_card'
        elif name == "/save" and len(raw_parts) >= 3:
            title = command_text.removeprefix(parts[0]).strip()
            tags = [tag.strip() for tag in raw_parts[2].split(",") if tag.strip()] if len(raw_parts) > 2 else []
            arguments = {"title": title, "content": raw_parts[1], "tags": tags}
            tool_name = "save_card"
        else:
            return None
        return ToolCall(call_id=f"mock-{uuid.uuid4().hex[:10]}", name=tool_name, arguments=arguments)
