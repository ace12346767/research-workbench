import asyncio
import json

import httpx
import pytest

from agent_workbench.config import AppConfig
from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider
from agent_workbench.tests.test_approval_flow import make_runtime


def provider_for(handler):
    return OpenAICompatibleProvider(base_url='https://provider.invalid/v1', api_key='sk-do-not-return',
                                     model='test-model', transport=httpx.MockTransport(handler))


def stream_response(tool=False, reasoning=False):
    delta = {'tool_calls': [{'index': 0, 'id': 'probe-1', 'function': {
        'name': 'diagnostic_echo', 'arguments': '{"value":"ping"}'}}]} if tool else {'content': 'OK'}
    if reasoning:
        delta['reasoning_content'] = 'private reasoning not for the diagnostic report'
    records = [dict(choices=[dict(delta=delta, finish_reason='tool_calls' if tool else 'stop')]),
               dict(choices=[], usage={'completion_tokens': 5})]
    return httpx.Response(200, text=''.join(f'data: {json.dumps(item)}\n\n' for item in records) + 'data: [DONE]\n\n')


def test_diagnostics_report_three_independent_capabilities_without_executing_tools():
    from agent_workbench.providers.diagnostics import diagnose_provider
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'test-model'}]})
        payload = json.loads(request.content)
        assert payload['max_tokens'] == 128
        assert len(payload['messages']) == 1
        if payload.get('tools'):
            assert [item['function']['name'] for item in payload['tools']] == ['diagnostic_echo']
        return stream_response(bool(payload.get('tools')), reasoning=True)
    provider = provider_for(handler)
    result = asyncio.run(diagnose_provider(provider))
    assert [check['status'] for check in result['checks']] == ['passed'] * 3
    assert result['checks'][1]['observed']['reasoning'] is True
    assert result['checks'][2]['observed']['usage'] is True
    assert len(requests) == 3
    assert provider.max_output_tokens is None
    assert 'private reasoning' not in json.dumps(result)
    assert 'sk-do-not-return' not in json.dumps(result)


def test_missing_models_endpoint_does_not_prevent_generation_probe():
    from agent_workbench.providers.diagnostics import diagnose_provider
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(404)
        return stream_response(bool(json.loads(request.content).get('tools')))
    result = asyncio.run(diagnose_provider(provider_for(handler)))
    assert result['checks'][0]['code'] == 'model_or_endpoint_not_found'
    assert result['checks'][1]['status'] == result['checks'][2]['status'] == 'passed'


@pytest.mark.parametrize('status,code', [(401, 'authentication_failed'), (403, 'authentication_failed'),
                                      (429, 'rate_limited'), (500, 'service_unavailable')])
def test_error_codes_are_stable_and_never_include_provider_secret_text(status, code):
    from agent_workbench.providers.diagnostics import diagnose_provider
    result = asyncio.run(diagnose_provider(provider_for(lambda request: httpx.Response(
        status, text='sk-do-not-return: sensitive diagnostic'))))
    assert result['checks'][0]['code'] == code
    assert 'sk-do-not-return' not in json.dumps(result)
    if status in (401, 403):
        assert [item['status'] for item in result['checks'][1:]] == ['skipped', 'skipped']


def test_text_instead_of_tool_call_is_not_mislabeled_as_incompatibility():
    from agent_workbench.providers.diagnostics import diagnose_provider
    def handler(request):
        return httpx.Response(200, json={'data': []}) if request.method == 'GET' else stream_response()
    result = asyncio.run(diagnose_provider(provider_for(handler)))
    assert result['checks'][2]['status'] == 'not_observed'
    assert result['checks'][2]['code'] == 'tool_call_not_observed'
    assert result['checks'][1]['observed']['reasoning'] is False


def test_timeout_closes_the_actual_provider_stream():
    from agent_workbench.providers.diagnostics import diagnose_provider
    async def scenario():
        closed = []
        class SlowProvider:
            async def list_models(self):
                return []
            async def stream(self, *args):
                try:
                    await asyncio.Event().wait()
                    yield None
                finally:
                    closed.append(True)
        result = await diagnose_provider(SlowProvider(), probe_timeout=0.02)
        assert [item['code'] for item in result['checks'][1:]] == ['timeout', 'timeout']
        assert len(closed) == 2
    asyncio.run(scenario())


def test_real_draft_requires_explicit_diagnostic_confirmation(tmp_path):
    runtime = make_runtime(tmp_path)
    draft = AppConfig(provider_mode='openai-compatible', base_url='https://provider.invalid/v1', model='test-model')
    with pytest.raises(ValueError, match='confirm'):
        asyncio.run(runtime.diagnose_provider(draft, 'sk-draft'))
    assert not runtime.config_repository.path.exists()


def test_diagnostic_uses_unsaved_draft_and_never_changes_conversation_or_config(tmp_path, monkeypatch):
    async def scenario():
        runtime = make_runtime(tmp_path)
        provider = provider_for(lambda request: httpx.Response(200, json={'data': []})
            if request.method == 'GET' else stream_response(bool(json.loads(request.content).get('tools'))))
        monkeypatch.setattr(runtime, 'make_provider', lambda *_: provider)
        before = runtime.provider
        result = await runtime.diagnose_provider(AppConfig(provider_mode='openai-compatible',
            base_url='https://provider.invalid/v1', model='test-model'), 'sk-draft', confirmed=True)
        assert result['mode'] == 'openai-compatible'
        assert runtime.provider is before
        assert not runtime.config_repository.path.exists()
        assert runtime.conversation_snapshot()['turns'] == []
        runtime.require_idle()
    asyncio.run(scenario())
