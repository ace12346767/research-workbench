from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from agent_workbench.router.effort_router import EffortRouter
from agent_workbench.router.semantic_router import DEFAULT_MIN_MARGIN, DEFAULT_MIN_SCORE, SemanticRouter
from agent_workbench.router.settings import GuidanceConfig


class OnnxE5Embedder:
    def __init__(self, model_dir: str | Path, *, max_length: int = 512) -> None:
        self.model_dir = Path(model_dir)
        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        pad_id = self.tokenizer.token_to_id("<pad>")
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding(pad_id=pad_id if pad_id is not None else 1, pad_token="<pad>")
        self.session = ort.InferenceSession(
            str(self.model_dir / "onnx" / "model_quantized.onnx"),
            providers=["CPUExecutionProvider"],
        )

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        encoded = self.tokenizer.encode_batch(texts)
        input_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
        attention_mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
        token_type_ids = np.asarray([item.type_ids for item in encoded], dtype=np.int64)
        hidden = self.session.run(
            ["last_hidden_state"],
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0]
        mask = attention_mask[:, :, None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        return pooled.astype(np.float32).tolist()


def create_default_effort_router(
    model_dir: str | Path,
    *,
    embedder: OnnxE5Embedder | None = None,
) -> EffortRouter:
    anchor_groups = GuidanceConfig().anchor_groups()
    embedder = embedder or OnnxE5Embedder(model_dir)
    semantic = SemanticRouter(
        anchor_groups=anchor_groups,
        embedder_factory=lambda: embedder,
        min_score=DEFAULT_MIN_SCORE,
        min_margin=DEFAULT_MIN_MARGIN,
        top_k=3,
        inference_timeout_ms=500,
    )
    return EffortRouter(semantic)
