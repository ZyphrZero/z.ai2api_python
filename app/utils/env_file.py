"""Helpers for updating .env files without dropping unrelated settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

_ENV_KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _serialize_env_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"

    text = "" if value is None else str(value)
    if not text:
        return ""

    if any(char.isspace() for char in text) or any(
        char in text for char in ["#", '"', "\\", "'"]
    ):
        if "'" not in text:
            return f"'{text}'"

        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return text


def update_env_file(
    updates: Mapping[str, object],
    env_path: str | Path = ".env",
) -> None:
    """Update selected keys inside a .env file while preserving other lines."""
    path = Path(env_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining_updates = {key: _serialize_env_value(value) for key, value in updates.items()}

    for index, line in enumerate(lines):
        match = _ENV_KEY_PATTERN.match(line)
        if not match:
            continue

        key = match.group(1)
        if key not in remaining_updates:
            continue

        lines[index] = f"{key}={remaining_updates.pop(key)}"

    if remaining_updates:
        if lines and lines[-1].strip():
            lines.append("")
        for key, value in remaining_updates.items():
            lines.append(f"{key}={value}")

    content = "\n".join(lines).rstrip()
    path.write_text(f"{content}\n" if content else "", encoding="utf-8")
