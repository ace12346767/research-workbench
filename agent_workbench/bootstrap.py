from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import MutableMapping

from agent_workbench.config import AppConfig, ConfigRepository
from agent_workbench.core.key_store import KeyStore
from agent_workbench.providers.budget import effective_limits
from agent_workbench.providers.mock import MockProvider
from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider
from agent_workbench.providers.reasoning import normalize_config, effective_reasoning
from agent_workbench.router.embedder import OnnxE5Embedder, create_default_effort_router
from agent_workbench.router.effort_router import EffortRouter
from agent_workbench.router.semantic_router import SemanticRouter
from agent_workbench.runtime import ApplicationRuntime


def load_env_file(path: str | Path, environment: MutableMapping[str, str] | None = None) -> None:
    target = Path(path)
    if not target.is_file():
        return
    env = environment if environment is not None else os.environ
    for raw_line in target.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            env.setdefault(key, value)


def config_from_environment(
    repository: ConfigRepository,
    environment: MutableMapping[str, str] | None = None,
) -> AppConfig:
    if repository.path.is_file():
        return repository.load()
    env = environment if environment is not None else os.environ
    base_url = env.get("OPENAI_BASE_URL", "").strip()
    model = env.get("OPENAI_MODEL", "").strip()
    mode = "openai-compatible" if base_url and model else "mock"
    return AppConfig(provider_mode=mode, base_url=base_url or "https://api.example.com/v1", model=model)


def resource_path(relative: str | Path) -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS")) / "agent_workbench"
    else:
        root = Path(__file__).resolve().parent
    return root / Path(relative)


def default_data_dir(environment: MutableMapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "AgentWorkbench"
    return Path.home() / ".agent-workbench"


def build_runtime(
    *,
    data_dir: str | Path | None = None,
    environment: MutableMapping[str, str] | None = None,
) -> ApplicationRuntime:
    env = environment if environment is not None else os.environ
    from agent_workbench.data_location import resolve_data_dir
    default = default_data_dir(env)
    pointer = default / 'data-location.json'
    selected = Path(data_dir) if data_dir is not None else resolve_data_dir(default, pointer)
    repository = ConfigRepository(selected / 'config.json')
    config = normalize_config(config_from_environment(repository, env))
    key_store = KeyStore()
    try:
        stored_key = key_store.get()
    except Exception:
        stored_key = None
    api_key = stored_key or env.get("OPENAI_API_KEY")
    if config.provider_mode == "openai-compatible" and api_key:
        provider = OpenAICompatibleProvider(
            base_url=config.base_url,
            api_key=api_key,
            model=config.model,
            max_output_tokens=effective_limits(config)['max_output_tokens'],
            reasoning_effort=effective_reasoning(config, None),
        )
    else:
        provider = MockProvider()

    model_dir = resource_path("assets/models/multilingual-e5-small")
    try:
        embedder = OnnxE5Embedder(model_dir)
        router = create_default_effort_router(model_dir, embedder=embedder)
    except Exception as exc:
        model_error = str(exc)
        async def unavailable_embedder(texts: list[str]) -> list[list[float]]:
            raise RuntimeError(f"local embedding model unavailable: {model_error}")

        semantic = SemanticRouter(anchor_groups=[], embedder_factory=lambda: unavailable_embedder)
        semantic.state = "unavailable"
        semantic.last_error = str(exc)
        router = EffortRouter(semantic)
        embedder = unavailable_embedder
    runtime = ApplicationRuntime(
        data_dir=repository.path.parent,
        router=router,
        provider=provider,
        embed=embedder,
        config_repository=repository,
        key_store=key_store,
        api_key=api_key,
        model_dir=model_dir,
    )
    runtime.config = config
    runtime.data_location_pointer = pointer if data_dir is None else None
    runtime.sync_conversation_provider()
    runtime.router.configure(config.guidance)
    return runtime
