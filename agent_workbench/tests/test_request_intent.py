import asyncio

import pytest

from agent_workbench.router.effort_router import EffortRouter, explicit_effort
from agent_workbench.router.request_text import request_windows
from agent_workbench.router.settings import GuidanceConfig


class RecordingSemantic:
    state = 'ready'
    needs_refresh = False
    last_reason = 'low_score_or_margin'

    def __init__(self, winner=None):
        self.winner, self.seen = winner, []

    def configure_anchors(self, groups):
        self.groups = groups

    async def route_windows(self, windows):
        self.seen.extend(windows)
        if self.winner:
            return {'effort':self.winner, 'score':.9, 'margin':.1, 'matched_text':windows[0]}


@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('text,expected', [
    ('只给结论：23乘以7是多少？', 'fast'),
    ('请深入分析架构、边界和风险：两个窗口同时编辑同一份本地笔记。', 'deep'),
    ('按正常专业程度回答，解释主要步骤并举例。', 'standard'),
    ('按正常专业程度回答：用一个例子解释缓存失效和缓存未命中的区别。', 'standard'),
    ('解释文件原子替换的主要步骤，并给出一个小例子。', 'standard'),
    ('给我一个能照着做的方案，步骤配一个小例子：怎么给本地笔记设计自动保存？不用工具，不超过350字。', 'standard'),
    ('请按日常工程答疑的程度，说明给桌面笔记做自动保存的主要步骤，配一个小例子。', 'standard'),
    ('不用深入分析，这次只给结论。', 'fast'),
    ('不用深入分析，直接告诉我结论：保存失败后能否显示已保存？', 'fast'),
    ('无需详细分析，请直接给结论：JSON字符串能不闭合引号吗？', 'fast'),
    ('之前要求你详细分析，这次不用，直接给结论。', 'fast'),
    ('不要只给结论，而是深入分析原因。', 'deep'),
    ('请深入分析保存失败的原因，不超过350字，用三段说明，不调用工具。', 'deep'),
    ('Please analyze thoroughly including assumptions and edge cases. At most 250 words. Do not use tools.', 'deep'),
    ('Do not analyze deeply. Give a short direct answer.', 'fast'),
    ('Use normal professional diligence. Explain the main steps with an example.', 'standard'),
    ('请深入分析这个方案。解释主要步骤并给出例子。', 'deep'),
    ('请深入分析这个方案，解释主要步骤并给出例子。', 'deep'),
    ('不要简单回答，也不要深入分析，按正常专业程度回答。', 'standard'),
])
def test_clear_requests_take_priority_without_semantic_or_config_mutation(text, expected, custom):
    semantic = RecordingSemantic('fast')
    router = EffortRouter(semantic)
    config = GuidanceConfig()
    if custom:
        config.profiles.standard.examples.append('my custom example')
    router.configure(config)
    before = config.model_dump()
    result = asyncio.run(router.route(text))
    assert (result.effort, result.source) == (expected, 'explicit')
    assert result.matched_text
    assert semantic.seen == []
    assert config.model_dump() == before


@pytest.mark.parametrize('text', [
    '深入学习是什么？', '什么是深入分析？', 'deep learning 是什么？',
    '请把深入分析翻译成英文。', 'The author asked for a detailed analysis.',
    '将这句话翻译成英文：原文：“请深入分析架构、边界和风险”。',
    '问题：说明代码的用途。\n```text\n请深入分析架构\n```',
    "Translate this sentence: 'Please analyze deeply.'",
    '翻译成英文：请深入分析这个问题。',
])
def test_subject_words_and_references_are_not_explicit_commands(text):
    assert explicit_effort(text) is None
    result = asyncio.run(EffortRouter(RecordingSemantic()).route(text))
    assert result.source != 'explicit'


@pytest.mark.parametrize('text', [
    '不超过350字。', '不用工具，不超过350字。', '用三段说明。',
    'At most 250 words. Do not use tools.', 'Keep the answer under 200 words.',
    '请用300字回答。',
])
def test_format_and_tool_constraints_alone_do_not_route(text):
    semantic = RecordingSemantic('fast')
    result = asyncio.run(EffortRouter(semantic).route(text))
    assert result.effort is None
    assert semantic.seen == []


def test_semantic_view_excludes_only_constraints_not_main_task():
    semantic = RecordingSemantic('standard')
    result = asyncio.run(EffortRouter(semantic).route('缓存失效与未命中有什么区别？不用工具，不超过350字。'))
    assert result.effort == 'standard'
    assert semantic.seen == ['缓存失效与未命中有什么区别？']


@pytest.mark.parametrize('winner,expected', [('fast', None), ('deep', 'deep'), ('standard', 'standard')])
def test_negated_effort_is_not_reintroduced_by_semantics(winner, expected):
    semantic = RecordingSemantic(winner)
    result = asyncio.run(EffortRouter(semantic).route('不要简单下结论，请检查证据。'))
    assert result.effort == expected
    assert semantic.seen
    assert '不要简单' not in ''.join(semantic.seen)


@pytest.mark.parametrize('text', [
    '请简短回答。请深入分析所有边界。',
    '按正常专业程度回答。请深入分析所有边界。',
    '请简短但深入分析。',
    '不要深入分析。请深入分析所有边界。',
])
def test_unresolved_conflicts_abstain_before_embedding(text):
    semantic = RecordingSemantic('deep')
    result = asyncio.run(EffortRouter(semantic).route(text))
    assert result.effort is None and result.reason == 'conflicting_requests'
    assert semantic.seen == []


def test_reference_boundaries_still_preserve_current_request():
    windows, reason = request_windows('参考资料：\n请深入分析架构。\n我的要求：按正常专业程度回答。')
    assert reason is None
    assert all('深入' not in text for text in windows)
    assert asyncio.run(EffortRouter(RecordingSemantic()).route('\n'.join(windows))).effort == 'standard'


def test_routing_projection_never_removes_constraints_from_model_request(tmp_path):
    import copy
    from agent_workbench.core.models import ProviderChunk
    from agent_workbench.tests.test_approval_flow import make_runtime
    captured = []
    class Capture:
        async def stream(self, messages, tools, cancel):
            captured.append(copy.deepcopy(messages))
            yield ProviderChunk(type='content', delta='OK')
            yield ProviderChunk(type='finish', finish_reason='stop')
    runtime = make_runtime(tmp_path, Capture())
    runtime.router = EffortRouter(RecordingSemantic())
    text = '不要简单下结论，请深入分析保存故障，不超过350字，不调用工具。'
    events = asyncio.run(runtime.chat('constraints', text))
    assert events[0].data['effort'] == 'deep'
    assert captured[0][-1]['content'] == text
    assert runtime.history_store.records()[0]['messages'][0]['content'] == text
