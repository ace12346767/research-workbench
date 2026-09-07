from __future__ import annotations

import asyncio

from agent_workbench.router.effort_router import EffortRouter, explicit_effort
from agent_workbench.router.semantic_router import SemanticRouter, rank_efforts


def test_explicit_effort_prefers_clear_user_request() -> None:
    assert explicit_effort("简单回答，不用展开") == "fast"
    assert explicit_effort("请深入分析架构、边界和风险") == "deep"
    assert explicit_effort("解释一下闭包") is None
    assert explicit_effort("请简短但深入分析") is None


def test_rank_efforts_abstains_when_margin_is_too_small() -> None:
    ranked = rank_efforts(
        query_vector=[1.0, 0.0],
        embedded_anchors=[
            ("deep", [1.0, 0.0]),
            ("standard", [0.999, 0.001]),
            ("fast", [-1.0, 0.0]),
        ],
        min_score=0.7,
        min_margin=0.01,
        top_k=1,
    )
    assert ranked is None


def test_router_abstains_while_cold_and_routes_after_warmup() -> None:
    async def embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            if "complex" in text or "复杂" in text:
                vectors.append([1.0, 0.0])
            elif "ordinary" in text or "常规" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([-1.0, 0.0])
        return vectors

    semantic = SemanticRouter(
        anchor_groups=[
            {"effort": "deep", "examples": ["复杂任务", "complex task"]},
            {"effort": "standard", "examples": ["常规任务", "ordinary task"]},
            {"effort": "fast", "examples": ["简单任务", "simple task"]},
        ],
        embedder_factory=lambda: embed,
        min_score=0.7,
        min_margin=0.1,
        top_k=1,
    )
    router = EffortRouter(semantic)

    cold = asyncio.run(router.route("complex system design"))
    assert cold.effort is None
    assert cold.source == "abstain"
    assert cold.reason == "cold"
    assert cold.router_state == "cold"

    asyncio.run(semantic.warmup())
    routed = asyncio.run(router.route("complex system design"))
    assert routed.effort == "deep"
    assert routed.source == "semantic"
    assert routed.router_state == "ready"


def test_explicit_effort_skips_semantic_router() -> None:
    calls = 0

    async def embed(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[1.0, 0.0] for _ in texts]

    semantic = SemanticRouter(
        anchor_groups=[{"effort": "deep", "examples": ["complex"]}],
        embedder_factory=lambda: embed,
    )
    router = EffortRouter(semantic)
    decision = asyncio.run(router.route("请深入分析这个问题"))

    assert decision.effort == "deep"
    assert decision.source == "explicit"
    assert calls == 0
