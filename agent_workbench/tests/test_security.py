from __future__ import annotations

from agent_workbench.core.security import sanitize_error_message


def test_error_sanitizer_removes_credentials_and_local_paths() -> None:
    message = "401 Authorization: Bearer top-secret at D:\\private\\project?api_key=also-secret"

    sanitized = sanitize_error_message(message)

    assert "top-secret" not in sanitized
    assert "also-secret" not in sanitized
    assert "D:\\private" not in sanitized
    assert "[redacted]" in sanitized
