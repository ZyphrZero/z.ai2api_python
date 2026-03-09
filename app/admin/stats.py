"""管理后台统计聚合辅助函数。"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import psutil

from app.services.request_log_dao import RequestLogDAO, get_request_log_dao
from app.services.token_dao import TokenDAO, get_token_dao
from app.utils.token_pool import TokenPool, get_token_pool

_TOKEN_POOL_SENTINEL = object()
DEFAULT_TREND_WINDOW = "7d"
TREND_WINDOW_OPTIONS = (
    {"key": "24h", "label": "24 小时"},
    {"key": "7d", "label": "7 天"},
    {"key": "30d", "label": "30 天"},
)


def _coerce_int(value: Any) -> int:
    """将数据库聚合结果安全转换为整数。"""
    return int(value or 0)


def calculate_success_rate(
    successful_requests: int,
    total_requests: int,
) -> float:
    """计算成功率百分比。"""
    if total_requests <= 0:
        return 0.0
    return round(successful_requests / total_requests * 100, 1)


def format_compact_number(value: Any) -> str:
    """格式化大数字，便于仪表盘展示。"""
    number = int(value or 0)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(number)


def normalize_trend_window(value: Any) -> str:
    """规范化趋势窗口参数，非法值回退到默认值。"""
    normalized = str(value or "").strip().lower()
    if normalized in {"24h", "7d", "30d"}:
        return normalized
    if normalized == "1d":
        return "24h"
    return DEFAULT_TREND_WINDOW


def format_uptime(total_seconds: int) -> str:
    """格式化运行时长。"""
    total_seconds = max(0, int(total_seconds))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}天")
    if days or hours:
        parts.append(f"{hours}小时")
    if days or hours or minutes:
        parts.append(f"{minutes}分钟")
    parts.append(f"{seconds}秒")

    return " ".join(parts)


def get_process_uptime() -> str:
    """获取当前进程运行时长。"""
    created_at = psutil.Process(os.getpid()).create_time()
    return format_uptime(int(time.time() - created_at))


async def collect_admin_stats(
    provider: str,
    *,
    token_dao: Optional[TokenDAO] = None,
    request_log_dao: Optional[RequestLogDAO] = None,
    token_pool: Any = _TOKEN_POOL_SENTINEL,
    trend_window: str = DEFAULT_TREND_WINDOW,
) -> Dict[str, Any]:
    """聚合管理后台所需的 Token 与请求统计。"""
    token_dao = token_dao or get_token_dao()
    request_log_dao = request_log_dao or get_request_log_dao()
    if token_pool is _TOKEN_POOL_SENTINEL:
        token_pool = get_token_pool()
    trend_window = normalize_trend_window(trend_window)

    token_counts = await token_dao.get_provider_token_counts(provider)
    request_stats = await request_log_dao.get_provider_request_stats(provider)
    usage_trend = await request_log_dao.get_provider_usage_trend(
        provider,
        window=trend_window,
    )

    pool_status: Dict[str, Any] = {}
    if isinstance(token_pool, TokenPool) or hasattr(token_pool, "get_pool_status"):
        pool_status = token_pool.get_pool_status() if token_pool else {}

    total_tokens = _coerce_int(token_counts.get("total_tokens"))
    enabled_tokens = _coerce_int(token_counts.get("enabled_tokens"))
    user_tokens = _coerce_int(token_counts.get("user_tokens"))
    guest_tokens = _coerce_int(token_counts.get("guest_tokens"))
    unknown_tokens = _coerce_int(token_counts.get("unknown_tokens"))

    pool_total_tokens = _coerce_int(pool_status.get("total_tokens"))
    if pool_total_tokens == 0 and token_pool is None:
        pool_total_tokens = max(0, enabled_tokens - guest_tokens)

    available_tokens = _coerce_int(pool_status.get("available_tokens"))
    healthy_tokens = _coerce_int(pool_status.get("healthy_tokens"))
    unhealthy_tokens = _coerce_int(pool_status.get("unhealthy_tokens"))

    total_requests = _coerce_int(request_stats.get("total_requests"))
    successful_requests = _coerce_int(request_stats.get("successful_requests"))
    failed_requests = _coerce_int(request_stats.get("failed_requests"))
    input_tokens = _coerce_int(request_stats.get("input_tokens"))
    output_tokens = _coerce_int(request_stats.get("output_tokens"))
    total_consumed_tokens = _coerce_int(request_stats.get("total_tokens"))
    cache_creation_tokens = _coerce_int(
        request_stats.get("cache_creation_tokens")
    )
    cache_read_tokens = _coerce_int(request_stats.get("cache_read_tokens"))
    cache_creation_requests = _coerce_int(
        request_stats.get("cache_creation_requests")
    )
    cache_hit_requests = _coerce_int(request_stats.get("cache_hit_requests"))
    average_latency = round(float(request_stats.get("avg_duration") or 0.0), 2)
    average_first_token_latency = round(
        float(request_stats.get("avg_first_token_time") or 0.0),
        2,
    )
    total_cache_tokens = cache_creation_tokens + cache_read_tokens

    return {
        "total_tokens": total_tokens,
        "enabled_tokens": enabled_tokens,
        "user_tokens": user_tokens,
        "guest_tokens": guest_tokens,
        "unknown_tokens": unknown_tokens,
        "pool_total_tokens": pool_total_tokens,
        "available_tokens": available_tokens,
        "healthy_tokens": healthy_tokens,
        "unhealthy_tokens": unhealthy_tokens,
        "total_requests": total_requests,
        "successful_requests": successful_requests,
        "failed_requests": failed_requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_consumed_tokens": total_consumed_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_cache_tokens": total_cache_tokens,
        "cache_creation_requests": cache_creation_requests,
        "cache_hit_requests": cache_hit_requests,
        "average_latency": average_latency,
        "average_first_token_latency": average_first_token_latency,
        "trend_window": trend_window,
        "usage_trend": usage_trend,
        "total_consumed_tokens_display": format_compact_number(
            total_consumed_tokens
        ),
        "total_cache_tokens_display": format_compact_number(
            total_cache_tokens
        ),
        "input_tokens_display": format_compact_number(input_tokens),
        "output_tokens_display": format_compact_number(output_tokens),
        "success_rate": calculate_success_rate(
            successful_requests,
            total_requests,
        ),
    }
