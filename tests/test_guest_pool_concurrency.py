import asyncio
import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from app.core import upstream as upstream_module
from app.core.upstream import UpstreamClient
from app.models.schemas import Message, OpenAIRequest
from app.utils.guest_session_pool import GuestSession, GuestSessionPool

POOL_SIZE = 8
REQUEST_COUNT = 64
REQUEST_DELAY_SECONDS = 0.03
FAILURE_POOL_SIZE = 4
FAILURE_REQUEST_COUNT = 24
FAILURE_DELAY_SECONDS = 0.02


def _make_session(user_id: str, token_suffix: str) -> GuestSession:
    return GuestSession(
        token=f"token-{token_suffix}",
        user_id=user_id,
        username=f"Guest-{user_id}",
    )


def _make_request() -> OpenAIRequest:
    return OpenAIRequest(
        model="GLM-4.5",
        messages=[Message(role="user", content="ping")],
        stream=False,
    )


@dataclass
class LoadState:
    active_posts: int = 0
    peak_posts: int = 0
    failed_once: set[str] = field(default_factory=set)


class FakeResponse:
    def __init__(self, status_code: int, text: str):
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
            return await handler(url, headers or {}, json or {})

    return FakeAsyncClient


async def _build_pool(monkeypatch, pool_size: int) -> GuestSessionPool:
    pool = GuestSessionPool(pool_size=pool_size)
    counter = 0

    async def fake_create_session() -> GuestSession:
        nonlocal counter
        counter += 1
        return _make_session(f"guest-{counter}", str(counter))

    monkeypatch.setattr(pool, "_create_session", fake_create_session)
    monkeypatch.setattr(pool, "_maintenance_loop", AsyncMock(return_value=None))
    monkeypatch.setattr(pool, "_delete_all_chats", AsyncMock(return_value=True))
    await pool.initialize()
    await asyncio.sleep(0)
    return pool


def _bind_guest_request_flow(
    client,
    pool: GuestSessionPool,
    assigned_user_ids: list[str],
):
    async def fake_transform_request(
        self,
        request,
        excluded_tokens=None,
        excluded_guest_user_ids=None,
    ):
        session = await pool.acquire(exclude_user_ids=excluded_guest_user_ids)
        assigned_user_ids.append(session.user_id)
        return {
            "url": f"https://upstream.test/{session.user_id}",
            "headers": {"x-guest-user-id": session.user_id},
            "body": {"model": request.model},
            "token": session.token,
            "chat_id": f"chat-{session.user_id}",
            "model": request.model,
            "user_id": session.user_id,
            "auth_mode": "guest",
            "token_source": "guest_pool",
            "guest_user_id": session.user_id,
        }

    async def fake_transform_response(self, response, request, transformed):
        return {
            "ok": response.is_success,
            "guest_user_id": transformed["guest_user_id"],
            "status_code": response.status_code,
        }

    client.transform_request = types.MethodType(fake_transform_request, client)
    client.transform_response = types.MethodType(fake_transform_response, client)


def _patch_upstream_globals(monkeypatch, pool: GuestSessionPool, async_client_cls):
    monkeypatch.setattr(upstream_module, "get_guest_session_pool", lambda: pool)
    monkeypatch.setattr(upstream_module, "get_token_pool", lambda: None)
    monkeypatch.setattr(upstream_module.settings, "ANONYMOUS_MODE", True)
    monkeypatch.setattr(upstream_module.httpx, "AsyncClient", async_client_cls)


def _build_handler(
    delay: float,
    state: LoadState,
    failure_users: set[str] | None = None,
):
    lock = asyncio.Lock()
    failures = failure_users or set()

    async def handler(url, headers, body):
        user_id = headers["x-guest-user-id"]
        async with lock:
            state.active_posts += 1
            state.peak_posts = max(state.peak_posts, state.active_posts)

        try:
            await asyncio.sleep(delay)
            if user_id in failures and user_id not in state.failed_once:
                state.failed_once.add(user_id)
                return FakeResponse(401, '{"message":"expired"}')
            return FakeResponse(200, "{}")
        finally:
            async with lock:
                state.active_posts -= 1

    return handler


@pytest.mark.asyncio
async def test_guest_pool_handles_many_concurrent_requests(monkeypatch):
    pool = await _build_pool(monkeypatch, POOL_SIZE)
    assigned_user_ids: list[str] = []
    state = LoadState()
    client = UpstreamClient()
    handler = _build_handler(REQUEST_DELAY_SECONDS, state)

    _bind_guest_request_flow(client, pool, assigned_user_ids)
    _patch_upstream_globals(
        monkeypatch,
        pool,
        _build_fake_async_client(handler),
    )

    results = await asyncio.gather(
        *(client.chat_completion(_make_request()) for _ in range(REQUEST_COUNT))
    )
    pool_status = pool.get_pool_status()

    assert all(result.get("ok") is True for result in results)
    assert len(set(assigned_user_ids)) == POOL_SIZE
    assert state.peak_posts >= POOL_SIZE
    assert pool_status == {
        "total_sessions": POOL_SIZE,
        "valid_sessions": POOL_SIZE,
        "available_sessions": POOL_SIZE,
        "busy_sessions": 0,
        "expired_sessions": 0,
    }

    await pool.close()


@pytest.mark.asyncio
async def test_guest_pool_recovers_from_failures_under_concurrency(monkeypatch):
    pool = await _build_pool(monkeypatch, FAILURE_POOL_SIZE)
    assigned_user_ids: list[str] = []
    state = LoadState()
    client = UpstreamClient()
    failure_users = {"guest-1", "guest-2"}
    handler = _build_handler(FAILURE_DELAY_SECONDS, state, failure_users)

    _bind_guest_request_flow(client, pool, assigned_user_ids)
    _patch_upstream_globals(
        monkeypatch,
        pool,
        _build_fake_async_client(handler),
    )

    results = await asyncio.gather(
        *(client.chat_completion(_make_request()) for _ in range(FAILURE_REQUEST_COUNT))
    )
    pool_status = pool.get_pool_status()
    current_user_ids = set(pool._sessions)

    assert all(result.get("ok") is True for result in results)
    assert state.failed_once == failure_users
    assert "guest-1" not in current_user_ids
    assert "guest-2" not in current_user_ids
    assert pool_status["busy_sessions"] == 0
    assert pool_status["valid_sessions"] == FAILURE_POOL_SIZE

    await pool.close()
