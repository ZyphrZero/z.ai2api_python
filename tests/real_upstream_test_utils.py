import os
from typing import Any

import pytest

from app.core import upstream as upstream_module
from app.core.upstream import UpstreamClient, _extract_user_id_from_token

REAL_AUTH_TOKEN_ENV = "REAL_AUTH_TOKEN_ENV"
RED_2X2_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR42mP4z8AARAwQCgAf7gP9Y167WwAAAABJRU5ErkJggg=="
)

def install_real_auth(monkeypatch) -> str:
    token = os.getenv(REAL_AUTH_TOKEN_ENV, "").strip()
    if not token:
        pytest.skip(f"需要设置环境变量 {REAL_AUTH_TOKEN_ENV}")

    user_id = _extract_user_id_from_token(token)
    if not user_id or user_id == "guest":
        raise AssertionError(f"{REAL_AUTH_TOKEN_ENV} 不是可解析的认证 token")

    async def fake_get_auth_info(
        self,
        excluded_tokens=None,
        excluded_guest_user_ids=None,
    ):
        return {
            "token": token,
            "user_id": user_id,
            "username": "RealUser",
            "auth_mode": "authenticated",
            "token_source": "env",
            "guest_user_id": None,
        }

    monkeypatch.setattr(UpstreamClient, "get_auth_info", fake_get_auth_info)
    monkeypatch.setattr(upstream_module, "get_token_pool", lambda: None)
    monkeypatch.setattr(upstream_module, "get_guest_session_pool", lambda: None)
    return token


def install_real_anonymous(monkeypatch) -> None:
    monkeypatch.setattr(upstream_module, "get_token_pool", lambda: None)
    monkeypatch.setattr(upstream_module, "get_guest_session_pool", lambda: None)
    monkeypatch.setattr(upstream_module.settings, "ANONYMOUS_MODE", True)


def extract_content(payload: dict[str, Any]) -> str:
    assert isinstance(payload, dict), payload
    assert "error" not in payload, payload

    choices = payload.get("choices") or []
    assert choices, payload

    message = choices[0].get("message") or {}
    content = str(message.get("content") or "").strip()
    assert content, payload
    return content


def assert_usage_present(payload: dict[str, Any]) -> None:
    usage = payload.get("usage") or {}
    assert int(usage.get("total_tokens") or 0) > 0, payload
