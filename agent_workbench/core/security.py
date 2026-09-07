from __future__ import annotations

import re


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_PARAM_RE = re.compile(r"(?i)(api[_-]?key|access[_-]?token)=([^&\s]+)")
_LOCAL_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s?]+")


def sanitize_error_message(error: BaseException | str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    message = _BEARER_RE.sub("Bearer [redacted]", message)
    message = _KEY_PARAM_RE.sub(lambda match: f"{match.group(1)}=[redacted]", message)
    message = _LOCAL_PATH_RE.sub("[local-path]", message)
    return message[:800] or "Unexpected error"
