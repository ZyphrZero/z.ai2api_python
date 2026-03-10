from urllib.parse import parse_qs, urlparse

import pytest

from app.core import upstream as upstream_module
from app.core.upstream import UpstreamClient
from app.models.schemas import Message, OpenAIRequest

FAKE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN",
    "X-FE-Version": "prod-fe-test",
    "Origin": "https://chat.z.ai",
    "Referer": "https://chat.z.ai/",
}


def _make_request(model: str) -> OpenAIRequest:
    return OpenAIRequest(
        model=model,
        messages=[Message(role="user", content="请用一句话回答：你好")],
        stream=True,
    )


async def _fake_get_auth_info(self, excluded_tokens=None, excluded_guest_user_ids=None):
    return {
        "token": "auth-token",
        "user_id": "user-123",
        "username": "User",
        "auth_mode": "authenticated",
        "token_source": "auth_pool",
        "guest_user_id": None,
    }


@pytest.mark.asyncio
async def test_glm47_request_bootstraps_chat_and_uses_browser_signature(monkeypatch):
    create_chat_calls: list[dict] = []
    browser_type_calls: list[str | None] = []

    def fake_headers(chat_id: str = "", browser_type=None):
        browser_type_calls.append(browser_type)
        headers = dict(FAKE_HEADERS)
        headers["Referer"] = (
            f"https://chat.z.ai/c/{chat_id}"
            if chat_id
            else FAKE_HEADERS["Referer"]
        )
        return headers

    async def fake_create_chat(
        self,
        *,
        prompt,
        model,
        token,
        headers,
        enable_thinking,
        web_search,
    ):
        create_chat_calls.append(
            {
                "prompt": prompt,
                "model": model,
                "token": token,
                "user_agent": headers["User-Agent"],
                "enable_thinking": enable_thinking,
                "web_search": web_search,
            }
        )
        return "persisted-chat-id"

    monkeypatch.setattr(UpstreamClient, "get_auth_info", _fake_get_auth_info)
    monkeypatch.setattr(UpstreamClient, "_create_upstream_chat", fake_create_chat)
    monkeypatch.setattr(upstream_module, "get_dynamic_headers", fake_headers)

    client = UpstreamClient()
    transformed = await client.transform_request(_make_request("GLM-4.7"))
    parsed_url = urlparse(transformed["url"])
    query = parse_qs(parsed_url.query)

    assert create_chat_calls == [
        {
            "prompt": "请用一句话回答：你好",
            "model": "glm-4.7",
            "token": "auth-token",
            "user_agent": FAKE_HEADERS["User-Agent"],
            "enable_thinking": False,
            "web_search": False,
        }
    ]
    assert browser_type_calls == ["chrome"]
    assert transformed["chat_id"] == "persisted-chat-id"
    assert transformed["headers"]["Accept"] == "*/*"
    assert transformed["headers"]["Referer"] == "https://chat.z.ai/c/persisted-chat-id"
    assert query["current_url"] == ["https://chat.z.ai/c/persisted-chat-id"]
    assert query["pathname"] == ["/c/persisted-chat-id"]
    assert query["user_agent"] == [FAKE_HEADERS["User-Agent"]]
    assert query["timezone"] == ["Asia/Shanghai"]
    assert transformed["body"]["chat_id"] == "persisted-chat-id"
    assert transformed["body"]["features"]["enable_thinking"] is False
    assert transformed["body"]["background_tasks"] == {
        "title_generation": True,
        "tags_generation": True,
    }
    assert "session_id" not in transformed["body"]
    assert "model_item" not in transformed["body"]


@pytest.mark.asyncio
async def test_glm47_thinking_defaults_to_enable_thinking(monkeypatch):
    create_chat_calls: list[dict] = []

    def fake_headers(chat_id: str = "", browser_type=None):
        headers = dict(FAKE_HEADERS)
        headers["Referer"] = (
            f"https://chat.z.ai/c/{chat_id}"
            if chat_id
            else FAKE_HEADERS["Referer"]
        )
        return headers

    async def fake_create_chat(
        self,
        *,
        prompt,
        model,
        token,
        headers,
        enable_thinking,
        web_search,
    ):
        create_chat_calls.append(
            {
                "model": model,
                "enable_thinking": enable_thinking,
                "web_search": web_search,
            }
        )
        return "thinking-chat-id"

    monkeypatch.setattr(UpstreamClient, "get_auth_info", _fake_get_auth_info)
    monkeypatch.setattr(UpstreamClient, "_create_upstream_chat", fake_create_chat)
    monkeypatch.setattr(upstream_module, "get_dynamic_headers", fake_headers)

    client = UpstreamClient()
    transformed = await client.transform_request(_make_request("GLM-4.7-Thinking"))

    assert create_chat_calls == [
        {
            "model": "glm-4.7",
            "enable_thinking": True,
            "web_search": False,
        }
    ]
    assert transformed["body"]["features"]["enable_thinking"] is True


@pytest.mark.asyncio
async def test_non_glm47_request_keeps_legacy_request_shape(monkeypatch):
    def fake_headers(chat_id: str = "", browser_type=None):
        headers = dict(FAKE_HEADERS)
        headers["Referer"] = (
            f"https://chat.z.ai/c/{chat_id}"
            if chat_id
            else FAKE_HEADERS["Referer"]
        )
        return headers

    async def fail_create_chat(self, **kwargs):
        raise AssertionError("GLM-4.5 不应触发 create_chat")

    monkeypatch.setattr(UpstreamClient, "get_auth_info", _fake_get_auth_info)
    monkeypatch.setattr(UpstreamClient, "_create_upstream_chat", fail_create_chat)
    monkeypatch.setattr(upstream_module, "get_dynamic_headers", fake_headers)

    client = UpstreamClient()
    transformed = await client.transform_request(_make_request("GLM-4.5"))
    query = parse_qs(urlparse(transformed["url"]).query)

    assert transformed["headers"]["Accept"] == "application/json"
    assert transformed["chat_id"] != "persisted-chat-id"
    assert "user_agent" not in query
    assert "session_id" in transformed["body"]
    assert transformed["body"]["model_item"]["name"] == "GLM-4.5"
