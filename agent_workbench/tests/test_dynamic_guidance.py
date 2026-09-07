import asyncio
import copy
import json

import httpx
import pytest

from agent_workbench.config import AppConfig, ConfigRepository
from agent_workbench.core.models import ProviderChunk, RouteDecision, ToolCall
from agent_workbench.core.loop import AgentLoop
from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider
from agent_workbench.router.effort_router import EffortRouter
from agent_workbench.router.semantic_router import SemanticRouter
from agent_workbench.server.chat_service import ChatService
from agent_workbench.tests.test_approval_flow import make_runtime
from agent_workbench.tools.registry import ToolRegistry


def test_guidance_defaults_are_separate_validated_and_backward_compatible(tmp_path):
    from agent_workbench.router.settings import GuidanceConfig
    config = AppConfig()
    assert config.guidance.enabled and not config.guidance.link_reasoning
    assert 'Handle this efficiently' in config.guidance.profiles.fast.prompt
    assert config.guidance.profiles.fast.examples
    other = GuidanceConfig()
    other.profiles.fast.examples.append('different')
    assert 'different' not in config.guidance.profiles.fast.examples
    with pytest.raises(ValueError):
        GuidanceConfig.model_validate({'profiles': {'fast': {'examples': [], 'prompt': 'ok'}}})
    with pytest.raises(ValueError):
        GuidanceConfig.model_validate({'profiles': {'fast': {'examples': ['ok'], 'prompt': ' '}}})
    repo = ConfigRepository(tmp_path / 'config.json')
    repo.path.write_text('{"provider_mode":"mock"}')
    assert repo.load().guidance == config.guidance
    repo.save(config)
    assert repo.load() == config


@pytest.mark.parametrize('text', [
    '> 请深入分析架构、边界和风险',
    '```text\n请深入分析架构、边界和风险\n```',
    '参考资料：\n请深入分析架构、边界和风险',
    '<reference>请深入分析架构、边界和风险</reference>',
    '原文：“请深入分析架构、边界和风险”',
    '他说要深入分析这份材料',
    '无需详细分析，请直接给结论',
    '之前要求你详细分析，这次不用，直接给结论。',
])
def test_reference_and_ambiguous_negation_do_not_trigger_deep(text):
    async def scenario():
        async def embed(texts):
            raise AssertionError('Reference-only or ambiguous input must not reach embedding')
        semantic = SemanticRouter(anchor_groups=[], embedder_factory=lambda: embed)
        router = EffortRouter(semantic)
        result = await router.route(text)
        assert result.effort != 'deep'
    asyncio.run(scenario())


def test_outer_request_wins_over_marked_reference_and_conflicts_abstain():
    async def scenario():
        semantic = SemanticRouter(anchor_groups=[], embedder_factory=lambda: None)
        router = EffortRouter(semantic)
        result = await router.route('简单回答。\n参考资料：\n请深入分析系统。')
        assert result.effort == 'fast'
        assert '深入' not in (result.matched_text or '')
        conflict = await router.route('请简短回答。\n请深入分析所有边界。')
        assert conflict.effort is None and conflict.reason == 'conflicting_requests'
    asyncio.run(scenario())


def test_overlapping_windows_cover_long_instructions_and_are_bounded():
    from agent_workbench.router.request_text import request_windows
    windows, reason = request_windows('abcd ' * 280)
    assert reason is None and len(windows) > 1
    assert all(len(window) <= 320 for window in windows)
    assert windows[0][-80:] == windows[1][:80]
    windows, reason = request_windows('x' * 100000)
    assert windows == [] and reason == 'routing_input_limit'


def test_window_semantics_matches_end_request_and_rejects_cross_window_conflict():
    async def scenario():
        async def embed(texts):
            return [[1., 0., 0.] if 'alpha' in text else [0., 1., 0.] if 'beta' in text else [0., 0., 1.] for text in texts]
        semantic = SemanticRouter(anchor_groups=[{'effort':'fast','examples':['alpha']},
            {'effort':'deep','examples':['beta']}, {'effort':'standard','examples':['gamma']}],
            embedder_factory=lambda: embed, min_score=.8, min_margin=.1, top_k=1)
        await semantic.warmup()
        router = EffortRouter(semantic)
        result = await router.route('beta ' * 170)
        assert result.effort == 'deep' and result.matched_text
        assert (await router.route('alpha.\nbeta.')).reason == 'conflicting_windows'
    asyncio.run(scenario())


def test_custom_examples_replace_old_anchors_and_disabled_guidance_does_not_embed():
    from agent_workbench.router.settings import GuidanceConfig
    async def scenario():
        calls = []
        async def embed(texts):
            calls.append(texts)
            return [[1., 0., 0.] if 'alpha' in text else [0., 1., 0.] if 'beta' in text else [0., 0., 1.] for text in texts]
        semantic = SemanticRouter(anchor_groups=[], embedder_factory=lambda: embed, min_score=.8, min_margin=.1, top_k=1)
        config = GuidanceConfig()
        for key, word in [('fast','alpha'),('standard','gamma'),('deep','beta')]:
            getattr(config.profiles, key).examples = [word]
        router = EffortRouter(semantic)
        router.configure(config)
        await semantic.warmup()
        assert (await router.route('alpha')).effort == 'fast'
        config.profiles.fast.examples, config.profiles.deep.examples = ['beta'], ['alpha']
        router.configure(config)
        assert (await router.route('alpha')).effort == 'deep'
        explicit = await router.route('请简短回答')
        assert explicit.source == 'explicit' and explicit.effort == 'fast'
        assert (await router.route('请深入分析这个问题')).effort == 'deep'
        config.enabled = False
        router.configure(config)
        before = len(calls)
        assert (await router.route('alpha')).reason == 'disabled'
        assert len(calls) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('model', ['deepseek-v4-pro', 'deepseek-v4-flash'])
def test_deepseek_uses_the_same_opt_in_linkage_and_preserves_manual_effort(model):
    from agent_workbench.providers.reasoning import reasoning_capabilities, normalize_config, effective_reasoning
    config = AppConfig(provider_mode='openai-compatible', model=model, reasoning_effort='max')
    assert not config.guidance.link_reasoning
    assert effective_reasoning(config, 'fast') == 'max'
    config.reasoning_effort = 'low'
    assert effective_reasoning(config, 'deep') == 'low'
    config.guidance.link_reasoning = True
    effective = normalize_config(config)
    assert effective.reasoning_effort == 'low'
    assert effective.guidance.link_reasoning
    assert reasoning_capabilities(config)['levels'] == ['low', 'high', 'max']
    assert [effective_reasoning(config, tier) for tier in ['fast', 'standard', 'deep', None]] == ['low', 'high', 'max', 'high']
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\n')
    async def scenario():
        for effort in ['low', 'high', 'max', None]:
            provider = OpenAICompatibleProvider(base_url='https://test.invalid/v1', api_key='test', model=model,
                reasoning_effort=effort, transport=httpx.MockTransport(handler))
            _ = [chunk async for chunk in provider.stream([{'role':'user','content':'test'}], [], asyncio.Event())]
    asyncio.run(scenario())
    assert [req.get('reasoning_effort') for req in requests] == ['low', 'high', 'max', None]
    assert all(req['thinking'] == {'type':'enabled'} for req in requests[:3])
    assert 'thinking' not in requests[-1], 'model default must not force API thinking parameters'


def test_known_model_link_maps_guidance_and_unknown_models_never_send_guessed_parameters():
    from agent_workbench.providers.reasoning import effective_reasoning, normalize_config
    config = AppConfig(provider_mode='openai-compatible', model='gpt-5', reasoning_effort='high')
    config.guidance.link_reasoning = True
    assert [effective_reasoning(config, level) for level in ('fast','standard','deep',None)] == ['low','medium','high','medium']
    config.guidance.link_reasoning = False
    assert effective_reasoning(config, 'fast') == 'high'
    config.model = 'unknown-reasoning-model'
    assert effective_reasoning(config, 'deep') is None
    assert normalize_config(config).reasoning_effort is None
    config.model = 'deepseek-reasoner'
    assert not normalize_config(config).guidance.link_reasoning
    assert effective_reasoning(config, 'fast') is None, 'legacy models must not be sent an unverified max parameter'


def test_guidance_is_injected_per_turn_without_mutating_user_content_or_persisting_in_history(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        runtime.config.guidance.profiles.standard.prompt = 'CUSTOM PROFESSIONAL GUIDANCE'
        class StandardRouter:
            async def route(self, text):
                return RouteDecision(effort='standard', source='semantic', reason='test')
        runtime.router = StandardRouter()
        requests = []
        class Capture:
            async def stream(self, messages, tools, cancel):
                requests.append(copy.deepcopy(messages))
                yield ProviderChunk(type='content', delta='OK')
                yield ProviderChunk(type='finish', finish_reason='stop')
        runtime.provider = Capture()
        await runtime.chat('a', 'original user request')
        assert requests[0][-1] == {'role':'user','content':'original user request'}
        assert 'CUSTOM PROFESSIONAL GUIDANCE' in requests[0][0]['content']
        assert 'CUSTOM PROFESSIONAL GUIDANCE' not in json.dumps(runtime.conversation.turns)
        runtime.config.guidance.enabled = False
        await runtime.chat('b', 'next request')
        assert 'CUSTOM PROFESSIONAL GUIDANCE' not in json.dumps(requests[1])
    asyncio.run(scenario())


def test_cancelled_warmup_can_be_retried():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        async def embed(texts):
            started.set()
            await release.wait()
            return [[1., 0.] for text in texts]
        router = SemanticRouter(anchor_groups=[{'effort':'standard', 'examples':['hello']}],
                                embedder_factory=lambda: embed)
        task = asyncio.create_task(router.warmup())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert router.state == 'cold'
        release.set()
        await router.warmup()
        assert router.state == 'ready'
    asyncio.run(scenario())


def test_reload_excludes_settings_changes_and_releases_guard_on_cancel(tmp_path, monkeypatch):
    async def scenario():
        runtime = make_runtime(tmp_path)
        runtime.model_dir = tmp_path
        started = asyncio.Event()
        async def embed(texts):
            started.set()
            await asyncio.Event().wait()
        monkeypatch.setattr('agent_workbench.router.embedder.OnnxE5Embedder', lambda path: embed)
        task = asyncio.create_task(runtime.reload_local_model())
        await started.wait()
        with pytest.raises(ValueError):
            runtime.update_settings(AppConfig())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        runtime.require_idle()
    asyncio.run(scenario())


@pytest.mark.parametrize('model,default', [('gpt-5.2', 'none'), ('gpt-5.4', 'none'), ('gpt-5.5', 'medium')])
def test_five_level_models_have_explicit_mapping_and_distinct_abstention_default(model, default):
    from agent_workbench.providers.reasoning import effective_reasoning, reasoning_capabilities
    config = AppConfig(provider_mode='openai-compatible', model=model, reasoning_effort='high')
    config.guidance.link_reasoning = True
    assert reasoning_capabilities(config)['levels'] == ['none', 'low', 'medium', 'high', 'xhigh']
    assert [effective_reasoning(config, tier) for tier in ['fast', 'standard', 'deep', None]] == ['low', 'medium', 'xhigh', default]
    config.guidance.link_reasoning = False
    assert effective_reasoning(config, 'deep') == 'high'


def test_reasoning_parameter_is_turn_local_and_uses_completion_budget_for_known_models():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\n')
    async def scenario():
        config = AppConfig(provider_mode='openai-compatible', model='gpt-5', reasoning_effort='high')
        config.guidance.link_reasoning = True
        provider = OpenAICompatibleProvider(base_url='https://test.invalid/v1', api_key='x', model='gpt-5',
            reasoning_effort='high', max_output_tokens=100, transport=httpx.MockTransport(handler))
        class Router:
            async def route(self, text):
                return RouteDecision(effort='fast', source='semantic', reason='test')
        for linked in [True, False]:
            config.guidance.link_reasoning = linked
            service = ChatService(router=Router(), loop=AgentLoop(provider=provider, tools=ToolRegistry()), config=config)
            await service.run('a', 'hello')
        assert provider.reasoning_effort == 'high'
    asyncio.run(scenario())
    assert [req['reasoning_effort'] for req in requests] == ['low', 'high']
    assert all(req['max_completion_tokens'] == 100 and 'max_tokens' not in req for req in requests)


def test_deepseek_tool_reasoning_roundtrips_without_showing_in_history(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path)
        calls = []
        class Provider:
            preserve_reasoning = True
            async def stream(self, messages, tools, cancel):
                calls.append(copy.deepcopy(messages))
                yield ProviderChunk(type='reasoning', delta='protocol-only reasoning')
                if len(calls) == 1:
                    yield ProviderChunk(type='tool_call', tool_call=ToolCall(call_id='tool1', name='ask_kb', arguments={'query':'test'}))
                    yield ProviderChunk(type='finish', finish_reason='tool_calls')
                else:
                    yield ProviderChunk(type='content', delta='final')
                    yield ProviderChunk(type='finish', finish_reason='stop')
        runtime.provider = Provider()
        await runtime.chat('a', 'look at my notes')
        assert calls[1][-2]['reasoning_content'] == 'protocol-only reasoning'
        assert 'protocol-only reasoning' not in json.dumps(runtime.conversation_snapshot())
        await runtime.chat('b', 'follow up')
        assert any(m.get('reasoning_content') for m in calls[2] if m['role']=='assistant')
    asyncio.run(scenario())
