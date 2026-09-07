from __future__ import annotations

import asyncio
from pathlib import Path

from agent_workbench.bootstrap import build_runtime, config_from_environment, load_env_file
from agent_workbench.config import ConfigRepository


def test_load_env_file_preserves_process_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=file-key\nOPENAI_MODEL=file-model\n", encoding="utf-8")
    environment = {"OPENAI_API_KEY": "process-key"}

    load_env_file(env_file, environment)

    assert environment["OPENAI_API_KEY"] == "process-key"
    assert environment["OPENAI_MODEL"] == "file-model"


def test_environment_config_uses_generic_openai_compatible_fields(tmp_path: Path) -> None:
    repository = ConfigRepository(tmp_path / "config.json")
    config = config_from_environment(
        repository,
        {
            "OPENAI_BASE_URL": "https://provider.example/v1",
            "OPENAI_MODEL": "general-reasoning-model",
            "OPENAI_API_KEY": "secret",
        },
    )

    assert config.provider_mode == "openai-compatible"
    assert config.model == "general-reasoning-model"
    assert not repository.path.exists()


def test_build_runtime_keeps_mock_chat_available_without_local_model(tmp_path: Path, monkeypatch) -> None:
    def missing_model(*args, **kwargs):
        raise FileNotFoundError("model is absent")

    monkeypatch.setattr("agent_workbench.bootstrap.OnnxE5Embedder", missing_model)
    runtime = build_runtime(data_dir=tmp_path / "data", environment={})

    asyncio.run(runtime.router.semantic_router.warmup())
    events = asyncio.run(runtime.chat("req-degraded", "hello"))

    assert runtime.local_model_status()["state"] == "unavailable"
    assert events[0].data["effort"] is None
    assert events[0].data["reason"] == "unavailable"
    assert any(event.type == "answer" for event in events)
