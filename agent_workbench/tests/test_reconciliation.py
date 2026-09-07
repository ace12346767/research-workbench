import asyncio
import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from agent_workbench.runtime import ApplicationRuntime
from agent_workbench.server.api import create_app
from agent_workbench.tests.test_approval_flow import embed, make_runtime


def save_card(runtime, title='Evidence', content='original evidence'):
    draft = runtime.cards.propose(title, content)
    return runtime.cards.approve(draft.approval_id)


def pdf_bytes():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), 'Paper evidence for a controlled workflow.')
        return document.tobytes()


def test_card_missing_index_is_reported_then_repaired_without_rewriting_file(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        card = save_card(runtime)
        before = Path(card.path).read_bytes()
        report = await runtime.audit_knowledge()
        assert report['issues'][0]['kind'] == 'card_index_missing'
        assert await runtime.knowledge.search('evidence') == []
        result = await runtime.repair_knowledge()
        assert result['after']['status'] == 'healthy'
        assert (await runtime.knowledge.search('evidence'))[0].text == 'original evidence'
        assert Path(card.path).read_bytes() == before
    asyncio.run(scenario())


def test_stale_card_repair_failure_preserves_last_index_and_reports_error(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        card = save_card(runtime)
        await runtime.repair_knowledge()
        runtime.cards.update(card.card_id, title='New title', content='new evidence')
        async def broken(texts):
            raise RuntimeError('embedder unavailable')
        runtime.knowledge.embed = broken
        result = await runtime.repair_knowledge()
        assert result['errors']
        assert result['after']['issues'][0]['kind'] == 'card_index_stale'
        runtime.knowledge.embed = embed
        assert (await runtime.knowledge.search('evidence'))[0].text == 'original evidence'
    asyncio.run(scenario())


def test_deleted_card_leaves_repairable_derived_index_only(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        card = save_card(runtime)
        await runtime.repair_knowledge()
        runtime.cards.delete(card.card_id)
        assert (await runtime.audit_knowledge())['issues'][0]['kind'] == 'orphan_index'
        assert (await runtime.repair_knowledge())['after']['status'] == 'healthy'
        assert await runtime.knowledge.search('evidence') == []
    asyncio.run(scenario())


def test_invalid_card_is_reported_without_pruning_last_committed_index(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        card = save_card(runtime)
        await runtime.repair_knowledge()
        Path(card.path).write_text('not a valid card', encoding='utf-8')
        report = await runtime.repair_knowledge()
        assert report['after']['issues'][0]['kind'] == 'invalid_card'
        assert not report['after']['issues'][0]['repairable']
        assert (await runtime.knowledge.search('evidence'))[0].text == 'original evidence'
    asyncio.run(scenario())


def test_corrupt_manifest_does_not_block_startup_or_get_overwritten(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.papers.manifest.write_text('{broken', encoding='utf-8')
    restarted = ApplicationRuntime(data_dir=runtime.data_dir, router=runtime.router,
                                   provider=runtime.provider, embed=embed)
    with TestClient(create_app(restarted)) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/api/kb/papers').json()['papers'] == []
        response = client.post('/api/kb/repair').json()
    assert response['after']['issues'][0]['kind'] == 'invalid_manifest'
    assert runtime.papers.manifest.read_text(encoding='utf-8') == '{broken'


def test_missing_paper_index_and_stale_manifest_counts_are_repaired(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        paper, _ = runtime.papers.stage('paper.pdf', pdf_bytes())
        assert (await runtime.audit_knowledge())['issues'][0]['kind'] == 'paper_index_missing'
        assert (await runtime.repair_knowledge())['after']['status'] == 'healthy'
        runtime.papers.update(paper.paper_id, chunk_count=99)
        assert (await runtime.audit_knowledge())['issues'][0]['kind'] == 'paper_state_mismatch'
        assert (await runtime.repair_knowledge())['after']['status'] == 'healthy'
        assert runtime.papers.get(paper.paper_id).chunk_count == 1
    asyncio.run(scenario())


def test_changed_or_missing_pdf_is_not_silently_reimported_or_pruned(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        paper = await runtime.import_pdf('paper.pdf', pdf_bytes())
        target = Path(paper['stored_path'])
        original = target.read_bytes()
        target.write_bytes(original + b'changed')
        report = await runtime.repair_knowledge()
        assert report['after']['issues'][0]['kind'] == 'paper_hash_mismatch'
        target.unlink()
        report = await runtime.repair_knowledge()
        assert report['after']['issues'][0]['kind'] == 'paper_missing'
        assert await runtime.knowledge.search('evidence')
    asyncio.run(scenario())


def test_unmanaged_index_and_external_manifest_paths_are_never_touched(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        outside = tmp_path / 'outside.pdf'
        outside.write_bytes(pdf_bytes())
        paper, _ = runtime.papers.stage('paper.pdf', pdf_bytes())
        runtime.papers.update(paper.paper_id, stored_path=str(outside))
        await runtime.knowledge.upsert(chunk_id='external', source_type='paper', title='External',
                                       text='evidence', source_path=str(outside))
        report = await runtime.repair_knowledge()
        assert any(issue['kind'] == 'unsafe_paper_path' for issue in report['after']['issues'])
        assert outside.exists()
        assert await runtime.knowledge.search('evidence')
    asyncio.run(scenario())


def test_repair_blocks_conflicting_mutations_and_unlocks_after_cancellation(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        card = save_card(runtime)
        entered = asyncio.Event()
        async def waiting(texts):
            entered.set()
            await asyncio.Event().wait()
        runtime.knowledge.embed = waiting
        task = asyncio.create_task(runtime.repair_knowledge())
        await asyncio.wait_for(entered.wait(), 2)
        try:
            with pytest.raises(ValueError):
                await runtime.delete_card(card.card_id)
            with pytest.raises(ValueError):
                await runtime.repair_knowledge()
            with pytest.raises(ValueError):
                runtime.set_workspace(tmp_path)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        runtime.set_workspace(tmp_path)
        assert Path(card.path).exists()
    asyncio.run(scenario())


def test_card_title_containing_separator_remains_readable(tmp_path):
    runtime = make_runtime(tmp_path)
    card = save_card(runtime, title='Before --- After')
    loaded = runtime.cards.list_cards()
    assert len(loaded) == 1
    assert loaded[0].card_id == card.card_id
    assert loaded[0].title == 'Before --- After'


def test_card_update_reports_applied_when_indexing_fails(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        card = save_card(runtime)
        async def broken(texts):
            raise RuntimeError('embedding unavailable')
        runtime.knowledge.embed = broken
        result = await runtime.update_card(card.card_id, title='Updated', content='new content')
        assert result['status'] == 'applied'
        assert result['index_status'] == 'failed'
        assert 'new content' in Path(card.path).read_text(encoding='utf-8')
    asyncio.run(scenario())


def test_repair_rejects_a_card_write_that_is_already_waiting_for_embedding(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        card = save_card(runtime)
        entered = asyncio.Event()
        async def waiting(texts):
            entered.set()
            await asyncio.Event().wait()
        runtime.knowledge.embed = waiting
        update = asyncio.create_task(runtime.update_card(card.card_id, title='Updated', content='pending'))
        await entered.wait()
        try:
            with pytest.raises(ValueError):
                await asyncio.wait_for(runtime.repair_knowledge(), timeout=0.05)
        finally:
            update.cancel()
            with pytest.raises(asyncio.CancelledError):
                await update
    asyncio.run(scenario())


@pytest.mark.parametrize('action', ['delete_paper', 'reindex_paper'])
def test_paper_mutation_rejects_manifest_paths_outside_managed_storage(tmp_path, action):
    async def scenario():
        runtime = make_runtime(tmp_path)
        outside = tmp_path / 'outside.pdf'
        outside.write_bytes(pdf_bytes())
        record, _ = runtime.papers.stage('paper.pdf', pdf_bytes())
        runtime.papers.update(record.paper_id, stored_path=str(outside))
        before = outside.read_bytes()
        with pytest.raises(ValueError):
            await getattr(runtime, action)(record.paper_id)
        assert outside.read_bytes() == before
    asyncio.run(scenario())
