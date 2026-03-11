from urllib.parse import parse_qs, urlparse

import pytest

from app.core import upstream as upstream_module
from app.core.upstream import UpstreamClient
from app.models.schemas import ContentPart, ImageUrl, Message, OpenAIRequest

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
        user_message_id,
        files,
        feature_entries,
        mcp_servers,
    ):
        create_chat_calls.append(
            {
                "prompt": prompt,
                "model": model,
                "token": token,
                "user_agent": headers["User-Agent"],
                "enable_thinking": enable_thinking,
                "web_search": web_search,
                "user_message_id": user_message_id,
                "files": files,
                "feature_entries": feature_entries,
                "mcp_servers": mcp_servers,
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

    assert len(create_chat_calls) == 1
    assert create_chat_calls[0]["prompt"] == "请用一句话回答：你好"
    assert create_chat_calls[0]["model"] == "glm-4.7"
    assert create_chat_calls[0]["token"] == "auth-token"
    assert create_chat_calls[0]["user_agent"] == FAKE_HEADERS["User-Agent"]
    assert create_chat_calls[0]["enable_thinking"] is False
    assert create_chat_calls[0]["web_search"] is False
    assert create_chat_calls[0]["files"] is None
    assert create_chat_calls[0]["feature_entries"] is None
    assert create_chat_calls[0]["mcp_servers"] is None
    assert create_chat_calls[0]["user_message_id"]
    assert browser_type_calls == ["chrome"]
    assert transformed["chat_id"] == "persisted-chat-id"
    assert transformed["headers"]["Accept"] == "*/*"
    assert transformed["headers"]["Referer"] == "https://chat.z.ai/c/persisted-chat-id"
    assert query["current_url"] == ["https://chat.z.ai/c/persisted-chat-id"]
    assert query["pathname"] == ["/c/persisted-chat-id"]
    assert query["user_agent"] == [FAKE_HEADERS["User-Agent"]]
    assert query["timezone"] == ["Asia/Shanghai"]
    assert transformed["body"]["chat_id"] == "persisted-chat-id"
    assert transformed["body"]["current_user_message_id"] == (
        create_chat_calls[0]["user_message_id"]
    )
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
        user_message_id,
        files,
        feature_entries,
        mcp_servers,
    ):
        create_chat_calls.append(
            {
                "model": model,
                "enable_thinking": enable_thinking,
                "web_search": web_search,
                "user_message_id": user_message_id,
                "files": files,
                "feature_entries": feature_entries,
                "mcp_servers": mcp_servers,
            }
        )
        return "thinking-chat-id"

    monkeypatch.setattr(UpstreamClient, "get_auth_info", _fake_get_auth_info)
    monkeypatch.setattr(UpstreamClient, "_create_upstream_chat", fake_create_chat)
    monkeypatch.setattr(upstream_module, "get_dynamic_headers", fake_headers)

    client = UpstreamClient()
    transformed = await client.transform_request(_make_request("GLM-4.7-Thinking"))

    assert len(create_chat_calls) == 1
    assert create_chat_calls[0]["model"] == "glm-4.7"
    assert create_chat_calls[0]["enable_thinking"] is True
    assert create_chat_calls[0]["web_search"] is False
    assert create_chat_calls[0]["files"] is None
    assert create_chat_calls[0]["feature_entries"] is None
    assert create_chat_calls[0]["mcp_servers"] is None
    assert create_chat_calls[0]["user_message_id"]
    assert transformed["body"]["features"]["enable_thinking"] is True
    assert transformed["body"]["current_user_message_id"] == (
        create_chat_calls[0]["user_message_id"]
    )


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


@pytest.mark.asyncio
async def test_glm46v_uses_persisted_chat_and_visual_features(monkeypatch):
    create_chat_calls: list[dict] = []
    upload_calls: list[dict] = []
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
        user_message_id,
        files,
        feature_entries,
        mcp_servers,
    ):
        create_chat_calls.append(
            {
                "prompt": prompt,
                "model": model,
                "token": token,
                "user_agent": headers["User-Agent"],
                "enable_thinking": enable_thinking,
                "web_search": web_search,
                "user_message_id": user_message_id,
                "files": files,
                "feature_entries": feature_entries,
                "mcp_servers": mcp_servers,
            }
        )
        return "vision-chat-id"

    async def fake_upload_image(
        self,
        data_url,
        chat_id,
        token,
        user_id,
        auth_mode="authenticated",
    ):
        upload_calls.append(
            {
                "data_url": data_url,
                "chat_id": chat_id,
                "token": token,
                "user_id": user_id,
                "auth_mode": auth_mode,
            }
        )
        return {
            "type": "image",
            "file": {
                "id": "file-id",
                "user_id": user_id,
                "filename": "file.png",
                "data": {},
                "meta": {
                    "name": "file.png",
                    "content_type": "image/png",
                    "size": 4,
                    "data": {},
                },
                "created_at": 1,
                "updated_at": 1,
            },
            "id": "file-id",
            "url": "/api/v1/files/file-id/content",
            "name": "file.png",
            "status": "uploaded",
            "size": 4,
            "error": "",
            "itemId": "item-id",
            "media": "image",
        }

    monkeypatch.setattr(UpstreamClient, "get_auth_info", _fake_get_auth_info)
    monkeypatch.setattr(UpstreamClient, "_create_upstream_chat", fake_create_chat)
    monkeypatch.setattr(UpstreamClient, "upload_image", fake_upload_image)
    monkeypatch.setattr(upstream_module, "get_dynamic_headers", fake_headers)

    client = UpstreamClient()
    request = OpenAIRequest(
        model="GLM-4.6V",
        messages=[
            Message(
                role="user",
                content=[
                    ContentPart(type="text", text="请判断图片主色调"),
                    ContentPart(
                        type="image_url",
                        image_url=ImageUrl(url="data:image/png;base64,AAAA"),
                    ),
                ],
            )
        ],
        stream=False,
    )

    transformed = await client.transform_request(request)
    query = parse_qs(urlparse(transformed["url"]).query)

    assert len(create_chat_calls) == 1
    assert create_chat_calls[0]["prompt"] == "请判断图片主色调"
    assert create_chat_calls[0]["model"] == "glm-4.6v"
    assert create_chat_calls[0]["token"] == "auth-token"
    assert create_chat_calls[0]["user_agent"] == FAKE_HEADERS["User-Agent"]
    assert create_chat_calls[0]["enable_thinking"] is True
    assert create_chat_calls[0]["web_search"] is False
    assert create_chat_calls[0]["feature_entries"] == upstream_module.GLM46V_SELECTED_FEATURES
    assert create_chat_calls[0]["mcp_servers"] == upstream_module.GLM46V_MCP_SERVERS
    assert create_chat_calls[0]["user_message_id"]
    assert create_chat_calls[0]["files"][0]["id"] == "file-id"
    assert create_chat_calls[0]["files"][0]["ref_user_msg_id"] == (
        create_chat_calls[0]["user_message_id"]
    )
    assert upload_calls == [
        {
            "data_url": "data:image/png;base64,AAAA",
            "chat_id": "",
            "token": "auth-token",
            "user_id": "user-123",
            "auth_mode": "authenticated",
        }
    ]
    assert browser_type_calls == ["chrome"]
    assert transformed["chat_id"] == "vision-chat-id"
    assert transformed["headers"]["Accept"] == "*/*"
    assert transformed["headers"]["Referer"] == "https://chat.z.ai/c/vision-chat-id"
    assert query["current_url"] == ["https://chat.z.ai/c/vision-chat-id"]
    assert query["pathname"] == ["/c/vision-chat-id"]
    assert query["user_agent"] == [FAKE_HEADERS["User-Agent"]]
    assert transformed["body"]["current_user_message_id"] == (
        create_chat_calls[0]["user_message_id"]
    )
    assert transformed["body"]["features"]["enable_thinking"] is True
    assert transformed["body"]["features"]["preview_mode"] is False
    assert "features" not in transformed["body"]["features"]
    assert transformed["body"]["mcp_servers"] == upstream_module.GLM46V_MCP_SERVERS
    assert transformed["body"]["files"][0]["id"] == "file-id"
    assert transformed["body"]["files"][0]["ref_user_msg_id"] == (
        create_chat_calls[0]["user_message_id"]
    )
    assert transformed["body"]["messages"][0]["content"][1]["image_url"]["url"] == (
        "file-id"
    )
    assert "session_id" not in transformed["body"]
