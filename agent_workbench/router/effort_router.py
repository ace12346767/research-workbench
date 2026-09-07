from __future__ import annotations

from agent_workbench.core.models import RouteDecision
from agent_workbench.router.semantic_router import SemanticRouter
from agent_workbench.router.request_text import request_intent
from agent_workbench.router.intent import effort_hits, STANDARD_MODE_RE
from agent_workbench.router.settings import GuidanceConfig


def explicit_effort(text: str) -> str | None:
    intent = request_intent(text or '')
    hits = resolved_hits(intent.windows)
    if intent.reason or len(hits) != 1 or hits & intent.excluded:
        return None
    return hits.pop()


def resolved_hits(windows):
    hits = set().union(*(effort_hits(window) for window in windows))
    if 'deep' in hits and not any(STANDARD_MODE_RE.search(window) for window in windows):
        hits.discard('standard')
    return hits


class EffortRouter:
    def __init__(self, semantic_router: SemanticRouter) -> None:
        self.semantic_router = semantic_router
        self.config = GuidanceConfig()

    def configure(self, config: GuidanceConfig) -> None:
        self.config = config.model_copy(deep=True)
        self.semantic_router.configure_anchors(config.anchor_groups())

    async def route(self, text: str) -> RouteDecision:
        state = self.semantic_router.state
        if not self.config.enabled:
            return RouteDecision(effort=None, source='abstain', reason='disabled', router_state=state)
        intent = request_intent(text)
        windows = intent.windows
        if intent.reason:
            return RouteDecision(effort=None, source='abstain', reason=intent.reason, router_state=state)
        explicit_hits = []
        combined = resolved_hits(windows)
        if len(combined) > 1 or combined & intent.excluded:
            return RouteDecision(effort=None, source='abstain', reason='conflicting_requests', router_state=state)
        for window in windows:
            hits = effort_hits(window) & combined
            if hits:
                explicit_hits.append((hits.pop(), window))
        if len({item[0] for item in explicit_hits}) > 1:
            return RouteDecision(effort=None, source='abstain', reason='conflicting_requests', router_state=state)
        if explicit_hits:
            explicit, matched = explicit_hits[0]
            return RouteDecision(
                effort=explicit,
                source="explicit",
                reason=f"explicit_{explicit}",
                router_state=self.semantic_router.state,
                matched_text=matched, window_count=len(windows),
            )
        if not text.strip():
            return RouteDecision(
                effort=None,
                source="abstain",
                reason="empty_or_context_only",
                router_state=self.semantic_router.state,
            )
        if state == 'cold' and self.semantic_router.needs_refresh:
            await self.semantic_router.warmup()
            state = self.semantic_router.state
        if state != "ready":
            reason = "cold" if state in {"cold", "loading"} else "unavailable"
            return RouteDecision(effort=None, source="abstain", reason=reason, router_state=state)
        semantic = await self.semantic_router.route_windows(windows)
        if semantic is None:
            return RouteDecision(
                effort=None,
                source="abstain",
                reason=self.semantic_router.last_reason or "low_score_or_margin",
                router_state=self.semantic_router.state,
            )
        if semantic['effort'] in intent.excluded:
            return RouteDecision(effort=None, source='abstain', reason='negated_effort', router_state=self.semantic_router.state)
        return RouteDecision(
            effort=semantic["effort"],
            source="semantic",
            score=float(semantic["score"]),
            margin=float(semantic["margin"]),
            reason="semantic_match",
            router_state=self.semantic_router.state,
            matched_text=semantic.get('matched_text'), window_count=len(windows),
        )
