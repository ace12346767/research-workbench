from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from agent_workbench.router.settings import GuidanceConfig


class AppConfig(BaseModel):
    provider_mode: Literal["mock", "openai-compatible"] = "mock"
    base_url: str = "https://api.example.com/v1"
    model: str = ""
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    guidance: GuidanceConfig = Field(default_factory=GuidanceConfig)
    dynamic_background: bool = False
    auto_classify_papers: bool = False


class ConfigRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload.pop("api_key", None)
        return AppConfig.model_validate(payload)

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = config.model_dump(mode="json")
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
