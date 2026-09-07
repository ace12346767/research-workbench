from __future__ import annotations

from typing import Protocol


class KeyringBackend(Protocol):
    def set_password(self, service: str, username: str, password: str) -> None: ...

    def get_password(self, service: str, username: str) -> str | None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class KeyStore:
    def __init__(
        self,
        *,
        backend: KeyringBackend | None = None,
        service: str = "AgentWorkbench",
        username: str = "provider-api-key",
    ) -> None:
        if backend is None:
            import keyring

            backend = keyring
        self.backend = backend
        self.service = service
        self.username = username

    def set(self, value: str) -> None:
        secret = value.strip()
        if not secret:
            raise ValueError("API key cannot be empty")
        self.backend.set_password(self.service, self.username, secret)

    def get(self) -> str | None:
        return self.backend.get_password(self.service, self.username)

    def delete(self) -> None:
        try:
            self.backend.delete_password(self.service, self.username)
        except Exception as exc:
            if exc.__class__.__name__ != "PasswordDeleteError":
                raise
