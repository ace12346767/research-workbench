from __future__ import annotations

import json
from pathlib import Path

from agent_workbench.config import AppConfig, ConfigRepository
from agent_workbench.core.key_store import KeyStore


def test_appearance_preference_survives_config_reload(tmp_path):
    repository = ConfigRepository(tmp_path / 'config.json')
    assert repository.load().dynamic_background is False
    repository.save(AppConfig(dynamic_background=True))
    assert repository.load().dynamic_background is True  # Legacy field remains readable.


class MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_config_repository_never_serializes_api_key(tmp_path: Path) -> None:
    repository = ConfigRepository(tmp_path / "config.json")
    config = AppConfig(provider_mode="openai-compatible", model="example-model")
    repository.save(config)

    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert "api_key" not in raw
    assert repository.load() == config


def test_key_store_uses_backend_instead_of_config_file() -> None:
    backend = MemoryBackend()
    store = KeyStore(backend=backend)

    store.set("secret")
    assert store.get() == "secret"
    store.delete()
    assert store.get() is None
