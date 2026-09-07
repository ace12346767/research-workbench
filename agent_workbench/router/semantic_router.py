from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Awaitable, Callable, Sequence
from typing import Any


Vector = Sequence[float]
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]
DEFAULT_MIN_SCORE = 0.835
DEFAULT_MIN_MARGIN = 0.013


def _normalized(vector: Vector) -> list[float] | None:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if not values or not math.isfinite(norm) or norm == 0:
        return None
    return [value / norm for value in values]


def _cosine(left: Vector, right: Vector) -> float | None:
    a = _normalized(left)
    b = _normalized(right)
    if a is None or b is None or len(a) != len(b):
        return None
    return sum(x * y for x, y in zip(a, b, strict=True))


def rank_efforts(
    query_vector: Vector,
    embedded_anchors: list[tuple[str, Vector]],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    top_k: int = 3,
) -> dict[str, float | str] | None:
    grouped: dict[str, list[float]] = {}
    for effort, vector in embedded_anchors:
        score = _cosine(query_vector, vector)
        if score is not None:
            grouped.setdefault(effort, []).append(score)
    ranked: list[tuple[str, float]] = []
    for effort, scores in grouped.items():
        selected = sorted(scores, reverse=True)[: max(1, top_k)]
        ranked.append((effort, sum(selected) / len(selected)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    if not ranked:
        return None
    winner, winner_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else -1.0
    margin = winner_score - runner_score
    if winner_score < min_score or margin < min_margin:
        return None
    return {"effort": winner, "score": winner_score, "margin": margin}


class SemanticRouter:
    def __init__(
        self,
        *,
        anchor_groups: list[dict[str, Any]],
        embedder_factory: Callable[[], Embedder | Awaitable[Embedder]],
        min_score: float = DEFAULT_MIN_SCORE,
        min_margin: float = DEFAULT_MIN_MARGIN,
        top_k: int = 3,
        inference_timeout_ms: int = 500,
    ) -> None:
        self.anchor_groups = anchor_groups
        self.embedder_factory = embedder_factory
        self.min_score = min_score
        self.min_margin = min_margin
        self.top_k = top_k
        self.inference_timeout_ms = inference_timeout_ms
        self.state = "cold"
        self.last_error: str | None = None
        self.last_reason: str | None = None
        self._embed: Embedder | None = None
        self._anchors: list[tuple[str, list[float]]] = []
        self.needs_refresh = False

    def configure_anchors(self, groups: list[dict]) -> None:
        if self.anchor_groups == groups:
            return
        self.anchor_groups = groups
        self._anchors = []
        if self.state != 'unavailable':
            self.state = 'cold'
            self.needs_refresh = self._embed is not None

    async def warmup(self) -> None:
        if self.state in {"ready", "unavailable"}:
            return
        self.state = "loading"
        try:
            candidate = self.embedder_factory()
            self._embed = await candidate if inspect.isawaitable(candidate) else candidate
            labels: list[str] = []
            texts: list[str] = []
            for group in self.anchor_groups:
                for example in group["examples"]:
                    labels.append(group["effort"])
                    texts.append(f"passage: {example}")
            vectors = await asyncio.wait_for(self._embed(texts), timeout=15)
            if len(vectors) != len(labels):
                raise ValueError("embedder returned the wrong number of anchor vectors")
            self._anchors = list(zip(labels, vectors, strict=True))
            self.state = "ready"
            self.last_reason = None
            self.needs_refresh = False
        except asyncio.CancelledError:
            self._anchors = []
            self.state = 'cold'
            self.needs_refresh = True
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self._embed = None
            self._anchors = []
            self.state = "unavailable"
            self.last_reason = "unavailable"

    async def route(self, text: str) -> dict[str, float | str] | None:
        return await self.route_windows([text])

    async def route_windows(self, windows: list[str]) -> dict[str, float | str] | None:
        if self.state != "ready" or self._embed is None or not windows:
            return None
        try:
            vectors = await asyncio.wait_for(
                self._embed([f"query: {text}" for text in windows]),
                timeout=min(3., self.inference_timeout_ms / 1000 * len(windows)),
            )
            if len(vectors) != len(windows):
                raise ValueError('Wrong window vector count')
            candidates = []
            for text, vector in zip(windows, vectors, strict=True):
                result = rank_efforts(vector, self._anchors, min_score=self.min_score,
                                      min_margin=self.min_margin, top_k=self.top_k)
                if result:
                    candidates.append({**result, 'matched_text': text})
            if len({item['effort'] for item in candidates}) > 1:
                self.last_reason = 'conflicting_windows'
                return None
            result = max(candidates, key=lambda item: item['score']) if candidates else None
            self.last_reason = None if result is not None else "low_score_or_margin"
            return result
        except TimeoutError as exc:
            self.last_error = str(exc)
            self.last_reason = "timeout"
            return None
        except Exception as exc:
            self.last_error = str(exc)
            self.last_reason = "error"
            return None
