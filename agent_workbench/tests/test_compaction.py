import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from agent_workbench.config import AppConfig
from agent_workbench.core.models import ProviderChunk
from agent_workbench.server.api import create_app
from agent_workbench.tests.test_approval_flow import make_runtime
from agent_workbench.tests.test_desktop_v11 import restart


class SummaryProvider:
    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

    async def stream(self, messages, tools, cancel):
        self.requests.append(copy.deepcopy((messages, tools)))
        if 'conversation compaction' in messages[0]['content']:
            if self.fail:
                raise RuntimeError('summary unavailable')
            yield ProviderChunk(type='reasoning', delta='PRIVATE_SUMMARY_REASONING')
            yield ProviderChunk(type='content', delta='## Constraints\nDo not force max.\n## Progress\nUse SQLite.\n## Next\nTest upgrade.')
        else:
            yield ProviderChunk(type='content', delta='Main answer')
        yield ProviderChunk(type='usage', usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120})
        yield ProviderChunk(type='finish', finish_reason='stop')


def seed(runtime, count=10, size=1800):
    for i in range(count):
        runtime.conversation.append(f'User {i}', [
            {'role':'user','content':f'User {i}: ' + 'evidence ' * (size // 9)},
            {'role':'assistant','content':'Use SQLite. Do not force max. ' + 'detail ' * (size // 7),
             'reasoning_content':'PRIVATE_ORIGINAL_REASONING'},
        ])


def test_auto_compaction_replaces_old_prefix_with_persistent_semantic_checkpoint(tmp_path):
    provider = SummaryProvider()
    runtime = make_runtime(tmp_path, provider)
    seed(runtime)
    runtime.config = AppConfig(context_window=16000, max_output_tokens=1024)
    events = asyncio.run(runtime.chat('new', 'Continue testing'))
    checkpoints = [e for e in events if e.type == 'compaction']
    assert any(e.data['status'] == 'completed' for e in checkpoints)
    request = provider.requests[-1][0]
    assert any('<compacted-summary>' in (m.get('content') or '') for m in request)
    assert request[-1]['content'] == 'Continue testing'
    assert 'PRIVATE_SUMMARY_REASONING' not in json.dumps(request)
    restored = restart(runtime)
    assert len(restored.conversation_snapshot()['turns']) == 11
    checkpoint = restored.history_store.checkpoint()
    assert checkpoint['summary'].startswith('## Constraints')
    assert checkpoint['through_id'] > 0
    assert 'PRIVATE_ORIGINAL_REASONING' not in json.dumps(provider.requests[:-1])


def test_manual_compaction_and_summary_api_do_not_add_chat_turns(tmp_path):
    runtime = make_runtime(tmp_path, SummaryProvider())
    seed(runtime, 6)
    with TestClient(create_app(runtime)) as client:
        result = client.post('/api/context/compact')
        assert result.status_code == 200
        assert result.json()['status'] == 'completed'
        summary = client.get('/api/context/summary').json()
        assert summary['checkpoint']['summary'].startswith('## Constraints')
        assert summary['usage']['total_tokens'] > 0
        assert len(client.get('/api/conversation').json()['turns']) == 6
        assert client.post('/api/context/estimate', json={'message':''}).json()['summary_kind'] == 'semantic'


def test_failed_compaction_keeps_checkpoint_and_original_history(tmp_path):
    runtime = make_runtime(tmp_path, SummaryProvider(fail=True))
    seed(runtime, 5)
    before = runtime.history_store.records()
    with TestClient(create_app(runtime)) as client:
        result = client.post('/api/context/compact')
        assert result.status_code == 400
    assert runtime.history_store.records() == before
    assert runtime.history_store.checkpoint() is None


def test_summary_is_session_scoped_and_clear_removes_checkpoint(tmp_path):
    runtime = make_runtime(tmp_path, SummaryProvider())
    seed(runtime, 5)
    asyncio.run(runtime.compact_context())
    old = runtime.history_store.session_id
    runtime.new_session()
    assert runtime.history_store.checkpoint() is None
    runtime.select_session(old)
    assert runtime.history_store.checkpoint() is not None
    runtime.clear_conversation()
    assert runtime.history_store.checkpoint() is None


def test_manual_compaction_busy_guard_and_cancellation(tmp_path):
    class WaitingProvider(SummaryProvider):
        async def stream(self, messages, tools, cancel):
            await asyncio.sleep(60)
            yield ProviderChunk(type='finish', finish_reason='stop')
    async def scenario():
        runtime = make_runtime(tmp_path, WaitingProvider())
        seed(runtime, 5)
        task = asyncio.create_task(runtime.compact_context())
        await asyncio.sleep(.05)
        with pytest.raises(ValueError):
            runtime.new_session()
        assert runtime.cancel_compaction()
        result = await asyncio.wait_for(task, 2)
        assert result['status'] == 'cancelled'
        assert runtime.history_store.checkpoint() is None
        assert len(runtime.history_store.records()) == 5
        runtime.new_session()
    asyncio.run(scenario())


def test_pruning_request_does_not_destroy_original_tool_result(tmp_path):
    runtime = make_runtime(tmp_path)
    (runtime.workspace.root / 'large.txt').write_text('x' * 30000)
    runtime.config = AppConfig(context_window=8192, max_output_tokens=256)
    asyncio.run(runtime.chat('read', '/read large.txt'))
    tools = [m for m in runtime.history_store.records()[0]['messages'] if m['role'] == 'tool']
    assert len(tools[0]['content']) == 30000


def test_history_lookup_reads_only_active_session_and_excludes_private_reasoning(tmp_path):
    runtime = make_runtime(tmp_path, SummaryProvider())
    seed(runtime, 2)
    result = asyncio.run(runtime.build_tools().execute('read_history', {'query':'SQLite'}))
    assert 'SQLite' in json.dumps(result)
    assert 'PRIVATE_ORIGINAL_REASONING' not in json.dumps(result)
    runtime.new_session()
    result = asyncio.run(runtime.build_tools().execute('read_history', {'query':'SQLite'}))
    assert result['matches'] == []


def test_second_compaction_merges_prior_checkpoint_without_replaying_covered_records(tmp_path):
    provider = SummaryProvider()
    runtime = make_runtime(tmp_path, provider)
    seed(runtime, 5)
    asyncio.run(runtime.compact_context())
    checkpoint = runtime.history_store.checkpoint()
    provider.requests.clear()
    seed(runtime, 5)
    asyncio.run(runtime.compact_context())
    assert runtime.history_store.checkpoint()['through_id'] > checkpoint['through_id']
    evidence = provider.requests[0][0][-1]['content']
    assert 'Previous checkpoint:\n## Constraints' in evidence
    assert '"record_id": 1,' not in evidence
    assert len(runtime.history_store.records()) == 10


@pytest.mark.parametrize('response', ['empty', 'length', 'tool_call', 'nonshrinking'])
def test_invalid_summary_never_replaces_originals(tmp_path, response):
    from agent_workbench.core.models import ToolCall
    class InvalidProvider:
        async def stream(self, messages, tools, cancel):
            if response == 'tool_call':
                yield ProviderChunk(type='tool_call', tool_call=ToolCall(call_id='bad',name='edit_file',arguments={}))
            elif response != 'empty':
                yield ProviderChunk(type='content',delta='x' * 30000 if response == 'nonshrinking' else 'partial')
            yield ProviderChunk(type='finish',finish_reason='length' if response == 'length' else 'stop')
    runtime = make_runtime(tmp_path, InvalidProvider())
    seed(runtime, 4)
    before = runtime.history_store.records()
    with pytest.raises(ValueError):
        asyncio.run(runtime.compact_context())
    assert runtime.history_store.checkpoint() is None
    assert runtime.history_store.records() == before
    assert (runtime.workspace.root / 'a.txt').read_text() == 'old'


def test_summary_requests_fit_budget_even_with_escaped_evidence(tmp_path):
    from agent_workbench.core.compaction import request_size
    class BudgetProvider(SummaryProvider):
        async def stream(self, messages, tools, cancel):
            assert request_size(messages[0], [], messages[1:], tools) <= 8192 - 1024 - 512
            async for chunk in super().stream(messages, tools, cancel):
                yield chunk
    runtime = make_runtime(tmp_path, BudgetProvider())
    runtime.config = AppConfig(context_window=8192, max_output_tokens=1024)
    runtime.conversation.append('escaped', [{'role':'user','content':'escaped'},
        {'role':'assistant','content':'\\"\n' * 5000}])
    asyncio.run(runtime.compact_context())
    assert runtime.history_store.checkpoint() is not None


def test_mock_never_claims_to_generate_a_semantic_summary(tmp_path):
    runtime = make_runtime(tmp_path)
    seed(runtime, 4)
    with pytest.raises(ValueError, match='Mock'):
        asyncio.run(runtime.compact_context())
    assert runtime.history_store.checkpoint() is None


def test_checkpoint_preserves_tool_pairs_in_recent_tail_and_estimate_is_read_only(tmp_path):
    provider = SummaryProvider()
    runtime = make_runtime(tmp_path, provider)
    seed(runtime, 4)
    runtime.conversation.append('read', [{'role':'user','content':'read'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'x','type':'function','function':{'name':'read_file','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'x','content':'important tool evidence'},
        {'role':'assistant','content':'read done'}])
    runtime.context_estimate('next')
    assert provider.requests == []
    asyncio.run(runtime.compact_context())
    asyncio.run(runtime.chat('next','continue'))
    request = provider.requests[-1][0]
    call = next(m for m in request if m.get('tool_calls'))
    result = next(m for m in request if m['role'] == 'tool')
    assert call['tool_calls'][0]['id'] == result['tool_call_id']


def test_summary_timeout_retains_originals(tmp_path):
    from agent_workbench.core.compaction import SemanticCompactor
    from agent_workbench.core.loop import SYSTEM_PROMPT
    class WaitingProvider:
        async def stream(self, messages, tools, cancel):
            await asyncio.sleep(60)
            yield ProviderChunk(type='finish', finish_reason='stop')
    runtime = make_runtime(tmp_path, WaitingProvider())
    seed(runtime, 4)
    compactor = SemanticCompactor(runtime.conversation, runtime.provider, runtime.context_budget(runtime.config))
    compactor.timeout = .01
    with pytest.raises(ValueError, match='timed out'):
        asyncio.run(compactor.prepare({'role':'system','content':SYSTEM_PROMPT},[],[],asyncio.Event(),force=True))
    assert runtime.history_store.checkpoint() is None


@pytest.mark.parametrize('code', ['context_length_exceeded', 'invalid_api_key'])
def test_real_adapter_recovers_only_canonical_overflow_and_preserves_manual_reasoning(tmp_path, code):
    import httpx
    from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider
    requests = []
    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(400, json={'error':{'code':code,'message':'upstream rejected request'}})
        compact = 'conversation compaction' in payload['messages'][0]['content']
        text = '## Constraints\nDo not force max.\n## Pending\nTest upgrade.' if compact else 'Answer after compaction'
        return httpx.Response(200, text='data: ' + json.dumps({'choices':[{'delta':{'content':text},'finish_reason':'stop'}]}) + '\n\ndata: [DONE]\n\n')
    provider = OpenAICompatibleProvider(base_url='https://test.invalid/v1',api_key='test-only',model='deepseek-v4-flash',
        reasoning_effort='high',max_output_tokens=1024,transport=httpx.MockTransport(handle))
    runtime = make_runtime(tmp_path, provider)
    runtime.config = AppConfig(provider_mode='openai-compatible',model='deepseek-v4-flash',context_window=32768,
                               reasoning_effort='high',max_output_tokens=1024)
    runtime.sync_conversation_provider()
    seed(runtime, 5)
    if code == 'invalid_api_key':
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(runtime.chat('next','continue'))
        assert len(requests) == 1
        assert runtime.history_store.checkpoint() is None
        return
    events = asyncio.run(runtime.chat('next','continue'))
    assert events[-1].data['finish_reason'] == 'stop'
    assert runtime.history_store.checkpoint() is not None
    assert all(p['reasoning_effort'] == 'high' for p in requests)
    summaries = [p for p in requests if 'conversation compaction' in p['messages'][0]['content']]
    assert summaries and all('tools' not in p for p in summaries)
    assert provider.max_output_tokens == 1024
    assert requests[-1]['max_tokens'] == 1024
    assert 'PRIVATE_ORIGINAL_REASONING' not in json.dumps(summaries)


def test_switch_to_small_window_can_shrink_an_existing_checkpoint_without_new_history(tmp_path):
    runtime = make_runtime(tmp_path, SummaryProvider())
    seed(runtime, 1)
    row = runtime.history_store.records()[0]
    runtime.history_store.save_checkpoint(row['id'], 'Old checkpoint detail. ' * 600, 'old-model',
                                         expected_through=0, session_id=runtime.history_store.session_id)
    runtime.config = AppConfig(context_window=8192, max_output_tokens=1024)
    asyncio.run(runtime.compact_context())
    summary = runtime.history_store.checkpoint()
    assert summary['through_id'] == row['id']
    assert len(summary['summary']) < 1000
    assert len(runtime.history_store.records()) == 1


def test_failed_incremental_summary_does_not_advance_existing_checkpoint(tmp_path):
    runtime = make_runtime(tmp_path, SummaryProvider())
    seed(runtime, 4)
    asyncio.run(runtime.compact_context())
    before = runtime.history_store.checkpoint()
    seed(runtime, 4)
    runtime.provider = SummaryProvider(fail=True)
    with pytest.raises(ValueError):
        asyncio.run(runtime.compact_context())
    assert runtime.history_store.checkpoint() == before
    assert len(runtime.history_store.records()) == 8


def test_summary_reads_public_tool_evidence_even_after_switching_provider(tmp_path):
    provider = SummaryProvider()
    runtime = make_runtime(tmp_path, provider)
    runtime.conversation.provider_key = 'old-provider'
    runtime.conversation.append('read', [{'role':'user','content':'read'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'old','type':'function','function':{'name':'read_file','arguments':'{}'}}],
         'reasoning_content':'PRIVATE_ORIGINAL_REASONING'},
        {'role':'tool','tool_call_id':'old','content':'UNIQUE_TOOL_EVIDENCE'},
        {'role':'assistant','content':'verified detail ' * 200}])
    runtime.sync_conversation_provider()
    asyncio.run(runtime.compact_context())
    evidence = json.dumps(provider.requests)
    assert 'UNIQUE_TOOL_EVIDENCE' in evidence
    assert 'PRIVATE_ORIGINAL_REASONING' not in evidence
