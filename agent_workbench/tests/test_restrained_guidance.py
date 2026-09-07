import asyncio
import json
import math
from pathlib import Path

from agent_workbench.config import AppConfig, ConfigRepository
from agent_workbench.router.embedder import create_default_effort_router
from agent_workbench.router.intent import effort_hits
from agent_workbench.router.settings import GuidanceConfig
from agent_workbench.router.semantic_router import rank_efforts, SemanticRouter


def test_user_accepted_thresholds_are_shared_by_factory_and_constructor(tmp_path):
    async def embed(texts):
        return [[1., 0.] for _ in texts]
    factory = create_default_effort_router(tmp_path, embedder=embed).semantic_router
    direct = SemanticRouter(anchor_groups=[], embedder_factory=lambda: embed)
    for router in (factory, direct):
        assert router.min_score == .835
        assert router.min_margin == .013


def test_default_ranking_uses_both_accepted_thresholds():
    def vector(score):
        return [score, math.sqrt(1 - score ** 2)]
    assert rank_efforts([1., 0.], [('fast', vector(.82)), ('deep', vector(.7))]) is None
    result = rank_efforts([1., 0.], [('fast', vector(.9)), ('deep', vector(.88))])
    assert result is not None and result['effort'] == 'fast'


def test_small_semantic_lead_is_not_enough_for_default_guidance():
    # Distinct but nearby tiers must not turn a weak preference into guidance.
    assert rank_efforts([1., 0.], [
        ('fast', [1., 0.]),
        ('standard', [.99, math.sqrt(1 - .99 ** 2)]),
        ('deep', [0., 1.]),
    ]) is None


def test_default_anchors_describe_preferences_without_effort_commands():
    config = GuidanceConfig()
    for group in config.anchor_groups():
        assert 4 <= len(group['examples']) <= 8
        for example in group['examples']:
            assert not effort_hits(example), example
    assert '我只想确认这一点，其他的先不讨论。' in config.profiles.fast.examples
    assert '我没理解中间是怎么联系起来的。' in config.profiles.standard.examples
    assert '有没有反例，或者同样说得通的其他解释？' in config.profiles.deep.examples


def test_default_guidance_limits_scope_and_preserves_explicit_user_requirements():
    for group in ('fast', 'standard', 'deep'):
        prompt = getattr(GuidanceConfig().profiles, group).prompt
        assert 'Do not expand the task' in prompt
        assert "Follow the user's explicit requirements" in prompt
    assert 'architecture' not in GuidanceConfig().profiles.deep.prompt.lower()


def test_factory_and_packaged_anchor_snapshot_share_settings_defaults(tmp_path):
    async def embed(texts):
        return [[1., 0.] for _ in texts]
    router = create_default_effort_router(tmp_path, embedder=embed)
    expected = GuidanceConfig().anchor_groups()
    assert router.semantic_router.anchor_groups == expected
    snapshot = Path(__file__).resolve().parents[1] / 'router/anchors.json'
    assert json.loads(snapshot.read_text(encoding='utf-8')) == expected


def test_unmatched_default_anchors_do_not_fall_back_to_standard(tmp_path):
    async def embed(texts):
        return [[1., 0.] if text.startswith('passage: ') else [0., 1.] for text in texts]
    async def scenario():
        router = create_default_effort_router(tmp_path, embedder=embed)
        await router.semantic_router.warmup()
        decision = await router.route('收到，谢谢。')
        assert decision.effort is None
        assert decision.source == 'abstain'
        assert decision.reason == 'low_score_or_margin'
    asyncio.run(scenario())


def test_saved_profiles_are_not_silently_replaced_by_new_defaults(tmp_path):
    config = AppConfig()
    config.guidance.profiles.fast.examples = ['只给结论', 'my saved preference']
    config.guidance.profiles.deep.prompt = 'My existing guidance'
    config.guidance.link_reasoning = True
    repository = ConfigRepository(tmp_path / 'config.json')
    repository.save(config)
    before = repository.path.read_bytes()
    assert repository.load() == config
    assert repository.path.read_bytes() == before
