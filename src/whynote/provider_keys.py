"""Read an explicitly configured private file without exposing secret values."""

import json
from pathlib import Path


def load_provider_key(path: str | Path, provider: str) -> str:
    if provider not in {"deepseek", "typesafe"} or not Path(path).is_absolute():
        raise ValueError("Provider key configuration is invalid")
    try:
        values = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        value = values.get(provider) if isinstance(values, dict) else None
        if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
            raise ValueError
    except (OSError, ValueError):
        raise ValueError("Provider key file is unavailable or the selected key is empty/invalid") from None
    return value
