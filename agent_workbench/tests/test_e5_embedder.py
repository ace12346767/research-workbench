from __future__ import annotations

import asyncio
import math
from pathlib import Path

from agent_workbench.router.embedder import OnnxE5Embedder, create_default_effort_router
from agent_workbench.router.settings import GuidanceConfig


MODEL_DIR = Path(__file__).parents[1] / "assets" / "models" / "multilingual-e5-small"


def test_onnx_e5_embedder_returns_normalized_384_vectors() -> None:
    embedder = OnnxE5Embedder(MODEL_DIR)
    vectors = asyncio.run(embedder(["query: hello", "passage: hello world"]))

    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert math.isclose(math.sqrt(sum(value * value for value in vectors[0])), 1.0, rel_tol=1e-5)


def test_default_router_can_abstain_on_tasks_without_expressed_preferences() -> None:
    router = create_default_effort_router(MODEL_DIR)
    asyncio.run(router.semantic_router.warmup())

    assert router.semantic_router.state == 'ready'
    # No mandatory task-to-tier mapping: absence of guidance is a valid outcome.
    assert asyncio.run(router.route("把这个标题转换成大写")).effort is None
    # The user accepted the calibrated operating point; retain the old boundary too.
    assert asyncio.run(router.route("帮我看看这个")).effort == 'fast'
    router.semantic_router.min_score = .52
    router.semantic_router.min_margin = .03
    assert asyncio.run(router.route("帮我看看这个")).effort is None


def test_each_default_tier_has_a_retrievable_anchor_with_real_e5() -> None:
    async def scenario():
        router = create_default_effort_router(MODEL_DIR)
        await router.semantic_router.warmup()
        for group in GuidanceConfig().anchor_groups():
            decisions = [await router.route(text) for text in group['examples']]
            assert any(d.effort == group['effort'] and d.source == 'semantic' for d in decisions), group['effort']
    asyncio.run(scenario())
