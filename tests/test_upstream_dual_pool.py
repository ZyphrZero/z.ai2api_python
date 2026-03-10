import asyncio
import types
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from app.core import upstream as upstream_module
from app.core.upstream import UpstreamClient
from app.models.schemas import Message, OpenAIRequest
from app.utils.guest_session_pool import GuestSession, GuestSessionPool

AUTH_POOL_SIZE = 2
GUEST_POOL_SIZE = 2
AUTH_REQUEST_COUNT = 6
MIXED_REQUEST_DELAY = 0.01


def _make_request() -> OpenAIRequest:
    return OpenAIRequest(
        model="GLM-4.5",
        messages=[Message(role="user", content="ping")],
        stream=False,
    )


def _make_guest_session(user_id: str) -> GuestSession:
    return GuestSession(
        token=f"guest-token-{user_id}",
        user_id=user_id,
        username=f"Guest-{user_id}",
    )


@dataclass
class StubTokenPool:
    tokens: list[str]

    def __post_init__(self):
        self.failure_tokens: list[str] = []
        self.success_tokens: list[str] = []

    def get_next_token(self, exclude_tokens=None):
        excluded = exclude_tokens or set()
        for token in self.tokens:
            if token not in excluded:
                return token
        return None

    async def record_token_failure(self, token: str, error=None, dao=None):
        self.failure_tokens.append(token)

    async def record_token_success(self, token: str, dao=None):
        self.success_tokens.append(token)

    def get_pool_status(self):
        return {"available_tokens": len(self.tokens)}


class FakeResponse:
    def __init__(self, status_code: int, text: str = "{}"):
        self.status_code = status_code
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def _build_fake_async_client(handler):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            return await handler(headers or {})

    return FakeAsyncClient


async def _build_guest_pool(
    monkeypatch,
    *,
    pool_size: int,
    user_ids: list[str],
) -> GuestSessionPool:
    pool = GuestSessionPool(pool_size=pool_size)
    queue = iter(user_ids)

    async def fake_create_session() -> GuestSession:
        return _make_guest_session(next(queue))

    monkeypatch.setattr(pool, "_create_session", fake_create_session)
    monkeypatch.setattr(pool, "_maintenance_loop", AsyncMock(return_value=None))
    monkeypatch.setattr(pool, "_delete_all_chats", AsyncMock(return_value=True))
    await pool.initialize()
    await asyncio.sleep(0)
    return pool


def _patch_upstream_dependencies(
    monkeypatch,
    *,
    token_pool,
    guest_pool,
    async_client_cls,
):
    monkeypatch.setattr(upstream_module, "get_token_pool", lambda: token_pool)
    monkeypatch.setattr(upstream_module, "get_guest_session_pool", lambda: guest_pool)
    monkeypatch.setattr(upstream_module.settings, "ANONYMOUS_MODE", True)
    monkeypatch.setattr(
        upstream_module.settings,
        "GUEST_POOL_SIZE",
        guest_pool.pool_size if guest_pool else 1,
    )
    monkeypatch.setattr(upstream_module.httpx, "AsyncClient", async_client_cls)


def _bind_minimal_request_flow(client: UpstreamClient, captures: list[dict]):
    async def fake_transform_request(
        self,
        request,
        excluded_tokens=None,
        excluded_guest_user_ids=None,
    ):
        auth_info = await self.get_auth_info(
            excluded_tokens=excluded_tokens,
            excluded_guest_user_ids=excluded_guest_user_ids,
        )
        captures.append(dict(auth_info))
        return {
            "url": "https://upstream.test/chat",
            "headers": {
                "x-token": str(auth_info["token"]),
                "x-token-source": str(auth_info["token_source"]),
                "x-guest-user-id": str(auth_info.get("guest_user_id") or ""),
            },
            "body": {"model": request.model},
            "token": auth_info["token"],
            "chat_id": "chat-id",
            "model": request.model,
            "user_id": auth_info["user_id"],
            "auth_mode": auth_info["auth_mode"],
            "token_source": auth_info["token_source"],
            "guest_user_id": auth_info["guest_user_id"],
        }

    async def fake_transform_response(self, response, request, transformed):
        return {
            "ok": response.is_success,
            "token_source": transformed["token_source"],
            "token": transformed["token"],
            "guest_user_id": transformed["guest_user_id"],
        }

    client.transform_request = types.MethodType(fake_transform_request, client)
    client.transform_response = types.MethodType(fake_transform_response, client)


async def _run_chat_requests(client: UpstreamClient, count: int) -> list[dict]:
    tasks = [client.chat_completion(_make_request()) for _ in range(count)]
    return await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_authenticated_tokens_are_used_before_guest_pool(monkeypatch):
    token_pool = StubTokenPool(["auth-1"])
    guest_pool = await _build_guest_pool(
        monkeypatch,
        pool_size=GUEST_POOL_SIZE,
        user_ids=["guest-1", "guest-2"],
    )
    captures: list[dict] = []
    acquire_calls = 0

    async def counted_acquire(*args, **kwargs):
        nonlocal acquire_calls
        acquire_calls += 1
        return await original_acquire(*args, **kwargs)

    async def handler(headers):
        await asyncio.sleep(MIXED_REQUEST_DELAY)
        return FakeResponse(200)

    client = UpstreamClient()
    original_acquire = guest_pool.acquire
    monkeypatch.setattr(guest_pool, "acquire", counted_acquire)
    _bind_minimal_request_flow(client, captures)
    _patch_upstream_dependencies(
        monkeypatch,
        token_pool=token_pool,
        guest_pool=guest_pool,
        async_client_cls=_build_fake_async_client(handler),
    )

    try:
        results = await _run_chat_requests(client, AUTH_REQUEST_COUNT)
        pool_status = guest_pool.get_pool_status()
    finally:
        await guest_pool.close()

    assert all(result["ok"] is True for result in results)
    assert all(item["token_source"] == "auth_pool" for item in captures)
    assert acquire_calls == 0
    assert token_pool.success_tokens == ["auth-1"] * AUTH_REQUEST_COUNT
    assert token_pool.failure_tokens == []
    assert pool_status["busy_sessions"] == 0
    assert pool_status["available_sessions"] == GUEST_POOL_SIZE


@pytest.mark.asyncio
async def test_authenticated_401_retries_next_token_before_guest_fallback(monkeypatch):
    token_pool = StubTokenPool(["auth-1", "auth-2"])
    guest_pool = await _build_guest_pool(
        monkeypatch,
        pool_size=GUEST_POOL_SIZE,
        user_ids=["guest-1", "guest-2"],
    )
    captures: list[dict] = []
    acquire_calls = 0

    async def counted_acquire(*args, **kwargs):
        nonlocal acquire_calls
        acquire_calls += 1
        return await original_acquire(*args, **kwargs)

    async def handler(headers):
        token = headers["x-token"]
        if token == "auth-1":
            return FakeResponse(401, '{"message":"expired"}')
        return FakeResponse(200)

    client = UpstreamClient()
    original_acquire = guest_pool.acquire
    monkeypatch.setattr(guest_pool, "acquire", counted_acquire)
    _bind_minimal_request_flow(client, captures)
    _patch_upstream_dependencies(
        monkeypatch,
        token_pool=token_pool,
        guest_pool=guest_pool,
        async_client_cls=_build_fake_async_client(handler),
    )

    try:
        result = await client.chat_completion(_make_request())
    finally:
        await guest_pool.close()

    assert result["ok"] is True
    assert [item["token"] for item in captures] == ["auth-1", "auth-2"]
    assert [item["token_source"] for item in captures] == ["auth_pool", "auth_pool"]
    assert token_pool.failure_tokens == ["auth-1"]
    assert token_pool.success_tokens == ["auth-2"]
    assert acquire_calls == 0


@pytest.mark.asyncio
async def test_authenticated_pool_exhaustion_falls_back_to_guest(monkeypatch):
    token_pool = StubTokenPool(["auth-1", "auth-2"])
    guest_pool = await _build_guest_pool(
        monkeypatch,
        pool_size=GUEST_POOL_SIZE,
        user_ids=["guest-1", "guest-2", "guest-3"],
    )
    captures: list[dict] = []

    async def handler(headers):
        if headers["x-token-source"] == "auth_pool":
            return FakeResponse(401, '{"message":"expired"}')
        return FakeResponse(200)

    client = UpstreamClient()
    _bind_minimal_request_flow(client, captures)
    _patch_upstream_dependencies(
        monkeypatch,
        token_pool=token_pool,
        guest_pool=guest_pool,
        async_client_cls=_build_fake_async_client(handler),
    )

    try:
        result = await client.chat_completion(_make_request())
        pool_status = guest_pool.get_pool_status()
    finally:
        await guest_pool.close()

    assert result["ok"] is True
    assert [item["token_source"] for item in captures] == [
        "auth_pool",
        "auth_pool",
        "guest_pool",
    ]
    assert token_pool.failure_tokens == ["auth-1", "auth-2"]
    assert token_pool.success_tokens == []
    assert result["guest_user_id"]
    assert pool_status["busy_sessions"] == 0


@pytest.mark.asyncio
async def test_guest_retry_is_isolated_and_does_not_pollute_auth_stats(monkeypatch):
    token_pool = StubTokenPool(["auth-1", "auth-2"])
    guest_pool = await _build_guest_pool(
        monkeypatch,
        pool_size=GUEST_POOL_SIZE,
        user_ids=["guest-1", "guest-2", "guest-3", "guest-4"],
    )
    captures: list[dict] = []

    async def handler(headers):
        source = headers["x-token-source"]
        guest_user_id = headers["x-guest-user-id"]
        if source == "auth_pool":
            return FakeResponse(401, '{"message":"expired"}')
        if guest_user_id == "guest-1":
            return FakeResponse(401, '{"message":"expired"}')
        return FakeResponse(200)

    client = UpstreamClient()
    _bind_minimal_request_flow(client, captures)
    _patch_upstream_dependencies(
        monkeypatch,
        token_pool=token_pool,
        guest_pool=guest_pool,
        async_client_cls=_build_fake_async_client(handler),
    )

    try:
        result = await client.chat_completion(_make_request())
        pool_status = guest_pool.get_pool_status()
    finally:
        await guest_pool.close()

    guest_ids = [
        item["guest_user_id"]
        for item in captures
        if item["token_source"] == "guest_pool"
    ]

    assert result["ok"] is True
    assert [item["token"] for item in captures[:2]] == ["auth-1", "auth-2"]
    assert token_pool.failure_tokens == ["auth-1", "auth-2"]
    assert token_pool.success_tokens == []
    assert guest_ids[0] == "guest-1"
    assert guest_ids[1] != "guest-1"
    assert pool_status["busy_sessions"] == 0
    assert pool_status["valid_sessions"] == GUEST_POOL_SIZE


@pytest.mark.asyncio
async def test_cleanup_idle_chats_only_touches_idle_valid_sessions(monkeypatch):
    guest_pool = await _build_guest_pool(
        monkeypatch,
        pool_size=3,
        user_ids=["guest-1", "guest-2", "guest-3"],
    )
    deleted_user_ids: list[str] = []

    async def fake_delete_all_chats(session: GuestSession):
        deleted_user_ids.append(session.user_id)
        return True

    monkeypatch.setattr(guest_pool, "_delete_all_chats", fake_delete_all_chats)
    guest_pool._sessions["guest-2"].active_requests = 1

    try:
        await guest_pool.cleanup_idle_chats()
        deleted_before_close = list(deleted_user_ids)
    finally:
        await guest_pool.close()

    assert deleted_before_close == ["guest-1", "guest-3"]


@pytest.mark.asyncio
async def test_report_failure_only_retires_target_guest_session(monkeypatch):
    guest_pool = await _build_guest_pool(
        monkeypatch,
        pool_size=3,
        user_ids=["guest-1", "guest-2", "guest-3", "guest-4"],
    )
    deleted_user_ids: list[str] = []

    async def fake_delete_all_chats(session: GuestSession):
        deleted_user_ids.append(session.user_id)
        return True

    monkeypatch.setattr(guest_pool, "_delete_all_chats", fake_delete_all_chats)

    try:
        await guest_pool.report_failure("guest-1")
        await asyncio.sleep(0)
        current_user_ids = set(guest_pool._sessions)
        deleted_before_close = list(deleted_user_ids)
    finally:
        await guest_pool.close()

    assert "guest-1" not in current_user_ids
    assert "guest-2" in current_user_ids
    assert "guest-3" in current_user_ids
    assert "guest-4" in current_user_ids
    assert deleted_before_close == ["guest-1"]
