import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agent_workbench.config import AppConfig
from agent_workbench.core.conversation import ContextBudget
from agent_workbench.providers.reasoning import reasoning_capabilities
from agent_workbench.server.api import create_app
from agent_workbench.tests.test_approval_flow import make_runtime
from agent_workbench.tests.test_conversation import CaptureProvider
from agent_workbench.runtime import ApplicationRuntime


def restart(runtime):
    return ApplicationRuntime(data_dir=runtime.data_dir, router=runtime.router, provider=runtime.provider,
                              embed=runtime.embed, key_store=runtime.key_store)


def test_explicit_version_alias_keeps_request_name_and_known_levels():
    config = AppConfig(provider_mode='openai-compatible', model='deepseek-v4-flash-0731')
    assert reasoning_capabilities(config)['levels'] == ['low', 'high', 'max']
    assert config.model == 'deepseek-v4-flash-0731'
    config.model = 'deepseek-v4-flash-made-up'
    assert not reasoning_capabilities(config)['can_link']


def test_complete_history_survives_restart_and_session_switch(tmp_path):
    runtime = make_runtime(tmp_path, CaptureProvider())
    async def chat():
        for i in range(12):
            await runtime.chat(str(i), f'question {i}')
    asyncio.run(chat())
    first = runtime.conversation_snapshot()
    assert len(first['turns']) == 12 and first['persistent']
    restored = restart(runtime)
    assert len(restored.conversation_snapshot()['turns']) == 12
    second = restored.new_session()
    assert second['session_id'] != first['session_id'] and not second['turns']
    restored.select_session(first['session_id'])
    assert len(restored.conversation_snapshot()['turns']) == 12
    restored.delete_session(first['session_id'])
    assert first['session_id'] not in {s['session_id'] for s in restored.list_sessions()}


def test_budget_does_not_label_dropped_history_as_a_semantic_summary():
    history = [[{'role':'user','content':'Decision: use SQLite. ' * 150},
                {'role':'assistant','content':'Verified source core/history.py. ' * 150}]]
    before = json.dumps(history)
    messages, info = ContextBudget(window=4096, output_tokens=256).fit(
        {'role':'system','content':'Rules'}, history.copy(), [{'role':'user','content':'Next'}], [])
    assert info['dropped_turns'] == 1
    assert info['compacted_turns'] == 0 and info['summary_kind'] is None
    assert info['estimator'] == 'utf8_bytes_conservative'
    assert not any('Earlier conversation excerpts' in item.get('content', '') for item in messages)
    assert json.dumps(history) == before


def test_workspace_modes_are_enforced_and_do_not_follow_new_directory(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.set_permission_mode('read_only')
    with pytest.raises(PermissionError):
        runtime.edit_workspace_file('a.txt', 'old', 'new')
    runtime.set_permission_mode('auto_edit')
    result = runtime.edit_workspace_file('a.txt', 'old', 'new')
    assert result['status'] == 'applied'
    assert (runtime.workspace.root / 'a.txt').read_text() == 'new'
    assert runtime.workspace_audit()
    (runtime.workspace.root / 'credentials.json').write_text('{"token":"old"}')
    with pytest.raises(PermissionError):
        runtime.edit_workspace_file('credentials.json', 'old', 'new')
    runtime.set_workspace(tmp_path / 'other')
    assert runtime.permission_mode == 'ask'


def test_card_draft_tool_is_nonblocking_persistent_and_requires_confirmation(tmp_path):
    runtime = make_runtime(tmp_path)
    async def scenario():
        result = await runtime.build_tools().execute('draft_card', {
            'title':'Storage decision', 'content':'Use SQLite', 'reason':'Reusable decision',
            'tags':['design'], 'sources':['conversation'],
        })
        assert result['status'] == 'pending'
        assert runtime.cards.list_cards() == []
        return result
    result = asyncio.run(scenario())
    restored = restart(runtime)
    assert restored.card_drafts.list()[0]['draft_id'] == result['draft_id']
    asyncio.run(restored.confirm_card_draft(result['draft_id']))
    assert len(restored.cards.list_cards()) == 1
    assert not restored.card_drafts.list()


def test_context_api_and_sessions_are_read_only_until_explicit_command(tmp_path):
    runtime = make_runtime(tmp_path)
    with TestClient(create_app(runtime)) as client:
        response = client.post('/api/context/estimate', json={'message':'Hello'})
        assert response.status_code == 200
        data = response.json()
        assert data['estimated_input'] > 0 and data['capacity'] is None
        assert data['estimator'] == 'utf8_bytes_conservative'
        assert client.get('/api/sessions').status_code == 200
        assert client.get('/api/conversation').json()['turns'] == []


def test_data_migration_copies_verifies_and_never_deletes_original(tmp_path):
    from agent_workbench.data_location import migrate_data, resolve_data_dir
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'config.json').write_text('{"model":"demo"}')
    (source / 'notes.txt').write_text('keep original')
    pointer = tmp_path / 'location.json'
    target = tmp_path / 'target'
    result = migrate_data(source, target, pointer)
    assert result['restart_required']
    assert (source / 'notes.txt').read_text() == (target / 'notes.txt').read_text()
    assert resolve_data_dir(source, pointer) == target
    with pytest.raises(ValueError):
        migrate_data(source, source / 'nested', pointer)
    with pytest.raises(ValueError):
        migrate_data(source, target, pointer)


def test_card_update_draft_rejects_changes_made_after_proposal(tmp_path):
    runtime = make_runtime(tmp_path)
    first = runtime.cards.propose('Title', 'original')
    runtime.cards.approve(first.approval_id)
    draft = runtime.card_drafts.propose('Title','proposed','update reason',update_card_id=first.card_id)
    runtime.cards.update(first.card_id,title='Title',content='changed elsewhere')
    with pytest.raises(ValueError,match='changed'):
        asyncio.run(runtime.confirm_card_draft(draft['draft_id']))
    assert runtime.card_drafts.get(draft['draft_id'])


def test_model_switch_keeps_display_history_without_foreign_protocol_fields(tmp_path):
    runtime = make_runtime(tmp_path, CaptureProvider())
    runtime.conversation.append('question',[{'role':'user','content':'question'},
        {'role':'assistant','content':'answer','reasoning_content':'private protocol'}])
    runtime.config.model = 'different-model'
    runtime.sync_conversation_provider()
    assert runtime.conversation_snapshot()['turns'][0]['assistant'] == 'answer'
    assert 'reasoning_content' not in json.dumps(runtime.conversation.turns)


def test_usage_sums_all_model_requests_not_only_last_tool_round(tmp_path):
    from agent_workbench.core.models import ProviderChunk, ToolCall
    class UsageProvider:
        async def stream(self, messages, tools, cancel):
            if messages[-1]['role'] == 'user':
                yield ProviderChunk(type='tool_call', tool_call=ToolCall(call_id='read',name='read_file',arguments={'path':'a.txt'}))
            else:
                yield ProviderChunk(type='content',delta='ok')
            yield ProviderChunk(type='usage',usage={'prompt_tokens':10,'completion_tokens':3,'total_tokens':13})
            yield ProviderChunk(type='finish',finish_reason='stop')
    runtime = make_runtime(tmp_path,UsageProvider())
    events = asyncio.run(runtime.chat('usage','read'))
    assert events[-1].data['usage']['total_tokens'] == 26
    assert runtime.conversation_snapshot()['usage']['total_tokens'] == 26


def test_provisional_user_record_exists_before_provider_finishes(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path, CaptureProvider())
        stream = runtime.chat_stream('pending', 'retain my request')
        await anext(stream)
        assert runtime.conversation_snapshot()['turns'][0]['user'] == 'retain my request'
        await stream.aclose()
        assert len(runtime.conversation_snapshot()['turns']) == 1
        assert runtime.conversation_snapshot()['turns'][0]['status'] == 'interrupted'
    asyncio.run(scenario())
