import asyncio
from pathlib import Path

import pytest

from agent_workbench.core.workspace import ApprovalRequired, Workspace
from agent_workbench.knowledge.cards import CardManager


def test_edit_preserves_bytes_beyond_preview_and_crlf(tmp_path):
    target = tmp_path / 'long.txt'
    original = b'old\r\n' + b'keep\r\n' * 600
    target.write_bytes(original)
    workspace = Workspace(tmp_path)
    with pytest.raises(ApprovalRequired) as error:
        workspace.propose_edit('long.txt', 'old', 'new')
    workspace.approve_edit(error.value.proposal.approval_id)
    assert target.read_bytes() == original.replace(b'old', b'new', 1)


def test_rejected_edit_cannot_be_approved(tmp_path):
    (tmp_path / 'a.txt').write_text('old')
    workspace = Workspace(tmp_path)
    with pytest.raises(ApprovalRequired) as error:
        workspace.propose_edit('a.txt', 'old', 'new')
    key = error.value.proposal.approval_id
    workspace.reject_edit(key)
    with pytest.raises(ValueError):
        workspace.approve_edit(key)
    assert (tmp_path / 'a.txt').read_text() == 'old'


def test_rejected_card_cannot_be_approved(tmp_path):
    manager = CardManager(tmp_path)
    draft = manager.propose('title', 'body')
    manager.reject(draft.approval_id)
    with pytest.raises(ValueError):
        manager.approve(draft.approval_id)
    assert list(tmp_path.glob('*.md')) == []


def test_card_delete_cannot_escape_directory(tmp_path):
    outside = tmp_path / 'outside.md'
    outside.write_text('---\ntitle: "outside"\n---\ntext')
    manager = CardManager(tmp_path / 'cards')
    with pytest.raises((ValueError, KeyError)):
        manager.delete('../outside')
    assert outside.exists()


def test_cancel_closes_silent_provider():
    from agent_workbench.core.loop import AgentLoop
    from agent_workbench.core.models import ProviderChunk
    from agent_workbench.tools.registry import ToolRegistry

    async def scenario():
        closed = asyncio.Event()
        cancel = asyncio.Event()
        class SilentProvider:
            async def stream(self, *args):
                try:
                    await asyncio.sleep(30)
                    yield ProviderChunk(type='content', delta='late')
                finally:
                    closed.set()
        loop = AgentLoop(provider=SilentProvider(), tools=ToolRegistry())
        task = asyncio.create_task(loop.run('cancel', 'hello', cancel))
        await asyncio.sleep(.02)
        cancel.set()
        events = await asyncio.wait_for(task, .5)
        assert closed.is_set()
        assert events[-1].data['finish_reason'] == 'cancelled'
    asyncio.run(scenario())
