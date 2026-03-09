import json
from datetime import datetime
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from app.admin import api as admin_api
from app.admin.stats import collect_admin_stats, format_uptime
from app.services import token_dao as token_dao_module
from app.services.request_log_dao import RequestLogDAO
from app.services.token_dao import TokenDAO
from app.utils import token_pool as token_pool_module
from app.utils.token_pool import TokenPool, sync_token_stats_to_db


class DummyPool:
    def __init__(self, status):
        self._status = status

    def get_pool_status(self):
        return self._status


def _make_get_request(path: str, query: dict[str, str] | None = None) -> Request:
    query_string = urlencode(query or {}).encode()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_collect_admin_stats_uses_request_logs_and_token_inventory(tmp_path):
    db_path = tmp_path / "admin_stats.db"
    token_dao = TokenDAO(str(db_path))
    await token_dao.init_database()
    request_log_dao = RequestLogDAO(str(db_path))

    await token_dao.add_token("zai", "token-user-1", validate=False)
    await token_dao.add_token("zai", "token-user-2", validate=False)
    await token_dao.add_token(
        "zai",
        "token-guest-1",
        token_type="guest",
        validate=False,
    )
    unknown_token_id = await token_dao.add_token(
        "zai",
        "token-unknown-1",
        token_type="unknown",
        validate=False,
    )
    await token_dao.update_token_status(int(unknown_token_id), False)

    await request_log_dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="pytest",
        protocol="openai",
        client_name="pytest",
        model="glm-5",
        status_code=200,
        success=True,
        duration=0.5,
        input_tokens=100,
        output_tokens=40,
        cache_read_tokens=20,
        total_tokens=140,
    )
    await request_log_dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="pytest",
        protocol="openai",
        client_name="pytest",
        model="glm-5",
        status_code=500,
        success=False,
        duration=1.2,
        input_tokens=60,
        output_tokens=10,
        cache_creation_tokens=15,
        total_tokens=70,
        error_message="upstream failed",
    )
    await request_log_dao.add_log(
        provider="zai",
        endpoint="/v1/messages",
        source="pytest",
        protocol="anthropic",
        client_name="pytest",
        model="glm-4.5",
        status_code=200,
        success=True,
        duration=0.9,
        input_tokens=30,
        output_tokens=20,
        total_tokens=50,
    )
    await request_log_dao.add_log(
        provider="other",
        endpoint="/ignored",
        source="pytest",
        protocol="openai",
        client_name="pytest",
        model="glm-ignored",
        status_code=200,
        success=True,
        duration=0.1,
    )

    stats = await collect_admin_stats(
        "zai",
        token_dao=token_dao,
        request_log_dao=request_log_dao,
        token_pool=DummyPool(
            {
                "total_tokens": 2,
                "available_tokens": 1,
                "healthy_tokens": 1,
                "unhealthy_tokens": 1,
            }
        ),
    )

    assert stats["total_tokens"] == 4
    assert stats["enabled_tokens"] == 3
    assert stats["user_tokens"] == 2
    assert stats["guest_tokens"] == 1
    assert stats["unknown_tokens"] == 1
    assert stats["pool_total_tokens"] == 2
    assert stats["available_tokens"] == 1
    assert stats["healthy_tokens"] == 1
    assert stats["unhealthy_tokens"] == 1
    assert stats["total_requests"] == 3
    assert stats["successful_requests"] == 2
    assert stats["failed_requests"] == 1
    assert stats["success_rate"] == pytest.approx(66.7)
    assert stats["input_tokens"] == 190
    assert stats["output_tokens"] == 70
    assert stats["total_consumed_tokens"] == 260
    assert stats["cache_creation_tokens"] == 15
    assert stats["cache_read_tokens"] == 20
    assert stats["total_cache_tokens"] == 35
    assert stats["cache_creation_requests"] == 1
    assert stats["cache_hit_requests"] == 1
    assert stats["average_latency"] == pytest.approx(0.87, rel=1e-2)
    assert stats["trend_window"] == "7d"
    assert len(stats["usage_trend"]) == 7
    assert stats["usage_trend"][-1]["total_tokens"] == 260
    assert stats["usage_trend"][-1]["cache_total_tokens"] == 35


@pytest.mark.asyncio
async def test_get_model_stats_from_db_includes_recent_same_day_logs(tmp_path):
    dao = RequestLogDAO(str(tmp_path / "request_logs.db"))

    await dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="pytest",
        protocol="openai",
        client_name="pytest",
        model="glm-5",
        status_code=200,
        success=True,
        duration=0.25,
        input_tokens=10,
        output_tokens=20,
    )

    stats = await dao.get_model_stats_from_db(hours=1)

    assert "glm-5" in stats
    assert stats["glm-5"]["total"] == 1
    assert stats["glm-5"]["success"] == 1
    assert stats["glm-5"]["failed"] == 0


@pytest.mark.asyncio
async def test_request_log_dao_supports_count_and_offset_pagination(tmp_path):
    dao = RequestLogDAO(str(tmp_path / "request_logs_paging.db"))

    for index in range(5):
        await dao.add_log(
            provider="zai",
            endpoint=f"/v1/chat/completions/{index}",
            source="pytest",
            protocol="openai",
            client_name="pytest",
            model="glm-5",
            status_code=200,
            success=True,
            duration=0.1,
        )

    total_count = await dao.count_logs(provider="zai")
    paged_logs = await dao.get_recent_logs(
        limit=2,
        offset=2,
        provider="zai",
    )

    assert total_count == 5
    assert len(paged_logs) == 2
    assert paged_logs[0]["endpoint"] == "/v1/chat/completions/2"
    assert paged_logs[1]["endpoint"] == "/v1/chat/completions/1"


@pytest.mark.asyncio
async def test_request_log_dao_returns_usage_trend_with_missing_days_filled(
    tmp_path,
):
    dao = RequestLogDAO(str(tmp_path / "request_logs_trend.db"))

    await dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="pytest",
        protocol="openai",
        client_name="pytest",
        model="glm-5",
        status_code=200,
        success=True,
        duration=0.2,
        input_tokens=12,
        output_tokens=8,
        cache_read_tokens=3,
        total_tokens=20,
    )

    trend = await dao.get_provider_usage_trend("zai", days=7)

    assert len(trend) == 7
    assert sum(day["total_requests"] for day in trend) == 1
    assert trend[-1]["total_tokens"] == 20
    assert trend[-1]["cache_total_tokens"] == 3


@pytest.mark.asyncio
async def test_request_log_dao_returns_hourly_usage_trend_with_missing_hours(
    tmp_path,
):
    dao = RequestLogDAO(str(tmp_path / "request_logs_hourly_trend.db"))
    log_id = await dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="pytest",
        protocol="openai",
        client_name="pytest",
        model="glm-5",
        status_code=200,
        success=True,
        duration=0.2,
        input_tokens=18,
        output_tokens=7,
        cache_creation_tokens=5,
        cache_read_tokens=3,
        total_tokens=25,
    )

    async with dao.get_connection() as conn:
        await conn.execute(
            "UPDATE request_logs SET timestamp = ? WHERE id = ?",
            ("2026-03-10 12:00:00", log_id),
        )
        await conn.commit()

    trend = await dao.get_provider_usage_trend(
        "zai",
        window="24h",
        now=datetime(2026, 3, 10, 12, 0, 0),
    )

    assert len(trend) == 24
    assert trend[-1]["label"] == "12:00"
    assert trend[-1]["tooltip_label"] == "2026-03-10 12:00"
    assert trend[-1]["input_tokens"] == 18
    assert trend[-1]["output_tokens"] == 7
    assert trend[-1]["cache_creation_tokens"] == 5
    assert trend[-1]["cache_read_tokens"] == 3
    assert sum(point["total_requests"] for point in trend) == 1
    assert all(point["total_requests"] == 0 for point in trend[:-1])


@pytest.mark.asyncio
async def test_dashboard_usage_trend_api_returns_requested_window(
    tmp_path,
    monkeypatch,
):
    dao = RequestLogDAO(str(tmp_path / "request_logs_api_trend.db"))
    log_id = await dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="pytest",
        protocol="openai",
        client_name="pytest",
        model="glm-5",
        status_code=200,
        success=True,
        duration=0.2,
        input_tokens=30,
        output_tokens=12,
        cache_read_tokens=4,
        total_tokens=42,
    )

    async with dao.get_connection() as conn:
        await conn.execute(
            "UPDATE request_logs SET timestamp = ? WHERE id = ?",
            ("2026-03-10 09:00:00", log_id),
        )
        await conn.commit()

    async def fixed_usage_trend(provider, days=None, *, window=None, now=None):
        return await RequestLogDAO.get_provider_usage_trend(
            dao,
            provider,
            days=days,
            window=window,
            now=datetime(2026, 3, 10, 12, 0, 0),
        )

    monkeypatch.setattr(dao, "get_provider_usage_trend", fixed_usage_trend)
    monkeypatch.setattr(admin_api, "get_request_log_dao", lambda: dao)
    request = _make_get_request(
        "/admin/api/dashboard/usage-trend",
        {"window": "24h"},
    )

    response = await admin_api.get_dashboard_usage_trend(request)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["window"] == "24h"
    assert len(payload["points"]) == 24
    assert payload["points"][-4]["input_tokens"] == 30
    assert payload["points"][-4]["cache_read_tokens"] == 4


@pytest.mark.asyncio
async def test_recent_logs_component_includes_usage_cache_and_latency_fields(
    tmp_path,
    monkeypatch,
):
    dao = RequestLogDAO(str(tmp_path / "request_logs_recent_component.db"))
    await dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="pytest",
        protocol="openai",
        client_name="pytest-client",
        model="glm-5",
        status_code=200,
        success=True,
        duration=1.25,
        first_token_time=0.42,
        input_tokens=111,
        output_tokens=22,
        cache_creation_tokens=9,
        cache_read_tokens=7,
        total_tokens=133,
    )

    monkeypatch.setattr(admin_api, "get_request_log_dao", lambda: dao)
    request = _make_get_request(
        "/admin/api/recent-logs",
        {"page": "1", "page_size": "12"},
    )

    response = await admin_api.get_recent_logs(request)
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "请求" in body
    assert "标记" in body
    assert "输入 / 输出" in body
    assert "缓存创建 / 命中" in body
    assert "用时 / 首字" in body
    assert "111" in body
    assert "22" in body
    assert "9" in body
    assert "7" in body
    assert "1.25s" in body
    assert "0.42s" in body


@pytest.mark.asyncio
async def test_recent_logs_component_deduplicates_client_and_source_labels(
    tmp_path,
    monkeypatch,
):
    dao = RequestLogDAO(str(tmp_path / "request_logs_recent_dedupe.db"))
    await dao.add_log(
        provider="zai",
        endpoint="/v1/chat/completions",
        source="browser",
        protocol="openai",
        client_name="Browser",
        model="glm-5",
        status_code=200,
        success=True,
        duration=1.0,
    )

    monkeypatch.setattr(admin_api, "get_request_log_dao", lambda: dao)
    request = _make_get_request(
        "/admin/api/recent-logs",
        {"page": "1", "page_size": "12"},
    )

    response = await admin_api.get_recent_logs(request)
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Browser" in body
    assert "OpenAI" in body
    assert "glm-5" in body
    assert ">browser<" not in body
    assert ">zai<" not in body


@pytest.mark.asyncio
async def test_token_dao_supports_count_and_offset_pagination(tmp_path):
    dao = TokenDAO(str(tmp_path / "tokens_paging.db"))
    await dao.init_database()

    for index in range(5):
        await dao.add_token("zai", f"token-{index}", validate=False)

    total_count = await dao.count_tokens_by_provider("zai", enabled_only=False)
    paged_tokens = await dao.get_tokens_by_provider(
        "zai",
        enabled_only=False,
        limit=2,
        offset=2,
    )

    assert total_count == 5
    assert len(paged_tokens) == 2
    assert paged_tokens[0]["token"] == "token-2"
    assert paged_tokens[1]["token"] == "token-3"


@pytest.mark.asyncio
async def test_token_pool_realtime_usage_stats_sync_to_db(tmp_path, monkeypatch):
    dao = TokenDAO(str(tmp_path / "token_usage.db"))
    await dao.init_database()
    token_id = await dao.add_token("zai", "token-usage", validate=False)
    assert token_id is not None

    pool = TokenPool([(token_id, "token-usage", "user")])

    await pool.record_token_success("token-usage", dao=dao)
    await pool.record_token_failure("token-usage", Exception("boom"), dao=dao)

    stats = await dao.get_token_stats(token_id)
    assert stats is not None
    assert stats["total_requests"] == 2
    assert stats["successful_requests"] == 1
    assert stats["failed_requests"] == 1

    monkeypatch.setattr(token_pool_module, "_token_pool", pool)
    monkeypatch.setattr(token_dao_module, "_token_dao", dao)

    await sync_token_stats_to_db()

    stats_after_sync = await dao.get_token_stats(token_id)
    assert stats_after_sync is not None
    assert stats_after_sync["total_requests"] == 2
    assert stats_after_sync["successful_requests"] == 1
    assert stats_after_sync["failed_requests"] == 1


def test_format_uptime_formats_seconds_minutes_and_hours():
    assert format_uptime(59) == "59秒"
    assert format_uptime(3661) == "1小时 1分钟 1秒"
