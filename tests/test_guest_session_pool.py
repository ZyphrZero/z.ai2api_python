import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.utils import guest_session_pool as guest_pool_module
from app.utils.guest_session_pool import GuestSession, GuestSessionPool


def _make_session(user_id: str, token_suffix: str) -> GuestSession:
    return GuestSession(
        token=f"token-{token_suffix}",
        user_id=user_id,
        username=f"Guest-{user_id}",
    )


@pytest.mark.asyncio
async def test_ensure_capacity_returns_when_only_duplicate_user_ids_are_created(
    monkeypatch,
):
    pool = GuestSessionPool(pool_size=2)
    create_calls = 0

    async def fake_create_session() -> GuestSession:
        nonlocal create_calls
        create_calls += 1
        return _make_session("duplicate-user", str(create_calls))

    monkeypatch.setattr(pool, "_create_session", fake_create_session)

    await asyncio.wait_for(pool._ensure_capacity(), timeout=0.2)

    assert create_calls >= 1
    assert set(pool._sessions) == {"duplicate-user"}
    assert len(pool._sessions) == 1


@pytest.mark.asyncio
async def test_initialize_logs_unique_session_count_when_results_contain_duplicates(
    monkeypatch,
):
    pool = GuestSessionPool(pool_size=3)
    sessions = [
        _make_session("user-1", "1"),
        _make_session("user-1", "2"),
        _make_session("user-2", "3"),
        _make_session("user-1", "4"),
        _make_session("user-2", "5"),
        _make_session("user-1", "6"),
        _make_session("user-2", "7"),
        _make_session("user-1", "8"),
        _make_session("user-2", "9"),
    ]
    info_mock = Mock()

    async def fake_create_session() -> GuestSession:
        return sessions.pop(0)

    monkeypatch.setattr(pool, "_create_session", fake_create_session)
    monkeypatch.setattr(pool, "_maintenance_loop", AsyncMock(return_value=None))
    monkeypatch.setattr(guest_pool_module.logger, "info", info_mock)
    monkeypatch.setattr(guest_pool_module.logger, "warning", Mock())

    await pool.initialize()
    await asyncio.sleep(0)

    assert set(pool._sessions) == {"user-1", "user-2"}
    assert any(
        call.args == ("✅ 匿名会话池初始化完成: 2 个会话",)
        for call in info_mock.call_args_list
    )


@pytest.mark.asyncio
async def test_acquire_skips_duplicate_excluded_session_without_overwriting_pool(
    monkeypatch,
):
    pool = GuestSessionPool(pool_size=2)
    existing = _make_session("user-1", "seed")
    pool._sessions[existing.user_id] = existing
    created_sessions = [
        _make_session("user-1", "duplicate"),
        _make_session("user-2", "fresh"),
    ]

    async def fake_create_session() -> GuestSession:
        return created_sessions.pop(0)

    monkeypatch.setattr(pool, "_create_session", fake_create_session)

    acquired = await pool.acquire(exclude_user_ids={"user-1"})

    assert acquired.user_id == "user-2"
    assert acquired.active_requests == 1
    assert set(pool._sessions) == {"user-1", "user-2"}
    assert pool._sessions["user-1"].token == "token-seed"
