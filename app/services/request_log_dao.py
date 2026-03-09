"""
请求日志数据访问层 (DAO)
提供请求日志的 CRUD 操作和查询功能
"""
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiosqlite

from app.models.request_log import DB_PATH, SQL_CREATE_REQUEST_LOGS_TABLE
from app.utils.logger import logger


def _format_sqlite_datetime(value: datetime) -> str:
    """格式化为 SQLite `CURRENT_TIMESTAMP` 兼容的时间字符串。"""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_trend_window(window: Optional[str], days: Optional[int]) -> str:
    """统一趋势窗口参数，兼容旧版 `days` 调用。"""
    if window:
        normalized = str(window).strip().lower()
    elif days == 30:
        normalized = "30d"
    elif days == 1:
        normalized = "24h"
    else:
        normalized = "7d"

    if normalized in {"24h", "7d", "30d"}:
        return normalized
    if normalized == "1d":
        return "24h"
    return "7d"


class RequestLogDAO:
    """请求日志数据访问对象"""

    def __init__(self, db_path: str = DB_PATH):
        """初始化 DAO"""
        self.db_path = db_path
        self._ensure_db_directory()
        self._init_db()

    def _ensure_db_directory(self):
        """确保数据库目录存在"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    def _init_db(self):
        """初始化数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.executescript(SQL_CREATE_REQUEST_LOGS_TABLE)
            self._ensure_columns(conn)
            conn.commit()
            conn.close()
            logger.debug("请求日志表初始化成功")
        except Exception as e:
            logger.error(f"初始化请求日志表失败: {e}")

    def _ensure_columns(self, conn: sqlite3.Connection):
        """为旧数据库补齐新增列。"""
        cursor = conn.execute("PRAGMA table_info(request_logs)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        required_columns = {
            "endpoint": "TEXT DEFAULT ''",
            "source": "TEXT DEFAULT 'unknown'",
            "protocol": "TEXT DEFAULT 'unknown'",
            "client_name": "TEXT DEFAULT 'Unknown'",
            "status_code": "INTEGER DEFAULT 200",
            "cache_creation_tokens": "INTEGER DEFAULT 0",
            "cache_read_tokens": "INTEGER DEFAULT 0",
        }

        for column, definition in required_columns.items():
            if column in existing_columns:
                continue
            conn.execute(
                f"ALTER TABLE request_logs ADD COLUMN {column} {definition}"
            )

    @asynccontextmanager
    async def get_connection(self):
        """获取异步数据库连接"""
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    async def add_log(
        self,
        provider: str,
        endpoint: str,
        source: str,
        protocol: str,
        client_name: str,
        model: str,
        status_code: int,
        success: bool,
        duration: float = 0.0,
        first_token_time: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        total_tokens: Optional[int] = None,
        error_message: str = None
    ) -> int:
        """
        添加请求日志

        Args:
            provider: 提供商名称
            endpoint: 请求端点
            source: 请求来源标识
            protocol: 协议类型
            client_name: 客户端名称
            model: 模型名称
            status_code: 请求状态码
            success: 是否成功
            duration: 总耗时（秒）
            first_token_time: 首字延迟（秒）
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            cache_creation_tokens: 缓存创建 token 数
            cache_read_tokens: 缓存命中 token 数
            total_tokens: 总 token 数
            error_message: 错误信息

        Returns:
            日志 ID
        """
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens

        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO request_logs
                (provider, endpoint, source, protocol, client_name, model,
                 status_code, success, duration, first_token_time,
                 input_tokens, output_tokens, cache_creation_tokens,
                 cache_read_tokens, total_tokens, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    endpoint,
                    source,
                    protocol,
                    client_name,
                    model,
                    status_code,
                    success,
                    duration,
                    first_token_time,
                    input_tokens,
                    output_tokens,
                    cache_creation_tokens,
                    cache_read_tokens,
                    total_tokens,
                    error_message,
                )
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_recent_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        provider: str = None,
        model: str = None,
        success: bool = None,
        source: str = None,
    ) -> List[Dict]:
        """
        获取最近的请求日志

        Args:
            limit: 返回数量限制
            provider: 过滤提供商
            model: 过滤模型
            success: 过滤成功/失败状态

        Returns:
            日志列表
        """
        query = "SELECT * FROM request_logs WHERE 1=1"
        params = []

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        if model:
            query += " AND model = ?"
            params.append(model)

        if success is not None:
            query += " AND success = ?"
            params.append(success)

        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, max(0, offset)])

        async with self.get_connection() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def count_logs(
        self,
        provider: str = None,
        model: str = None,
        success: bool = None,
        source: str = None,
    ) -> int:
        """统计日志总数。"""
        query = "SELECT COUNT(*) AS total_count FROM request_logs WHERE 1=1"
        params = []

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        if model:
            query += " AND model = ?"
            params.append(model)

        if success is not None:
            query += " AND success = ?"
            params.append(success)

        if source:
            query += " AND source = ?"
            params.append(source)

        async with self.get_connection() as conn:
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            return int(row["total_count"] or 0) if row else 0

    async def get_logs_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        provider: str = None,
        model: str = None
    ) -> List[Dict]:
        """
        按时间范围获取日志

        Args:
            start_time: 开始时间
            end_time: 结束时间
            provider: 过滤提供商
            model: 过滤模型

        Returns:
            日志列表
        """
        query = "SELECT * FROM request_logs WHERE timestamp BETWEEN ? AND ?"
        params = [
            _format_sqlite_datetime(start_time),
            _format_sqlite_datetime(end_time),
        ]

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        if model:
            query += " AND model = ?"
            params.append(model)

        query += " ORDER BY timestamp DESC, id DESC"

        async with self.get_connection() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_provider_request_stats(self, provider: Optional[str] = None) -> Dict:
        """聚合请求日志统计，可按提供商过滤。"""
        query = """
            SELECT
                COUNT(*) as total_requests,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_requests,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed_requests,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(cache_creation_tokens) as cache_creation_tokens,
                SUM(cache_read_tokens) as cache_read_tokens,
                SUM(
                    CASE WHEN cache_creation_tokens > 0 THEN 1 ELSE 0 END
                ) as cache_creation_requests,
                SUM(
                    CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END
                ) as cache_hit_requests,
                AVG(duration) as avg_duration,
                AVG(
                    CASE
                        WHEN first_token_time > 0 THEN first_token_time
                        ELSE NULL
                    END
                ) as avg_first_token_time
            FROM request_logs
        """
        params: List[object] = []

        if provider:
            query += " WHERE provider = ?"
            params.append(provider)

        try:
            async with self.get_connection() as conn:
                cursor = await conn.execute(query, params)
                row = await cursor.fetchone()

            if not row:
                return {
                    "total_requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_requests": 0,
                    "cache_hit_requests": 0,
                    "avg_duration": 0.0,
                    "avg_first_token_time": 0.0,
                }

            return {
                "total_requests": int(row["total_requests"] or 0),
                "successful_requests": int(row["successful_requests"] or 0),
                "failed_requests": int(row["failed_requests"] or 0),
                "input_tokens": int(row["input_tokens"] or 0),
                "output_tokens": int(row["output_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "cache_creation_tokens": int(
                    row["cache_creation_tokens"] or 0
                ),
                "cache_read_tokens": int(row["cache_read_tokens"] or 0),
                "cache_creation_requests": int(
                    row["cache_creation_requests"] or 0
                ),
                "cache_hit_requests": int(row["cache_hit_requests"] or 0),
                "avg_duration": float(row["avg_duration"] or 0.0),
                "avg_first_token_time": float(
                    row["avg_first_token_time"] or 0.0
                ),
            }
        except Exception as e:
            logger.error(f"❌ 获取请求统计失败: {e}")
            return {
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_requests": 0,
                "cache_hit_requests": 0,
                "avg_duration": 0.0,
                "avg_first_token_time": 0.0,
            }

    async def get_provider_usage_trend(
        self,
        provider: Optional[str] = None,
        days: Optional[int] = None,
        *,
        window: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> List[Dict]:
        """按窗口聚合最近一段时间的请求与 token 趋势。"""
        trend_window = _normalize_trend_window(window, days)
        current_time = now or datetime.utcnow()

        if trend_window == "24h":
            bucket_count = 24
            current_hour = current_time.replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            start_time = current_hour - timedelta(hours=bucket_count - 1)
            bucket_expression = "strftime('%Y-%m-%d %H:00:00', timestamp)"
            row_key = "trend_bucket"
            label_format = "%H:%M"
            tooltip_format = "%Y-%m-%d %H:00"
            rows = await self._query_usage_trend_rows(
                provider,
                start_time,
                bucket_expression,
                row_key,
            )
            rows_by_bucket = {str(row[row_key]): dict(row) for row in rows}
            trend: List[Dict] = []

            for offset in range(bucket_count):
                bucket_time = start_time + timedelta(hours=offset)
                bucket_key = bucket_time.strftime("%Y-%m-%d %H:00:00")
                trend.append(
                    self._build_usage_trend_point(
                        row=rows_by_bucket.get(bucket_key, {}),
                        bucket=bucket_key,
                        label=bucket_time.strftime(label_format),
                        tooltip_label=bucket_time.strftime(tooltip_format),
                    )
                )

            return trend

        bucket_count = 30 if trend_window == "30d" else 7
        current_date = current_time.date()
        start_date = current_date - timedelta(days=bucket_count - 1)
        start_time = datetime.combine(start_date, datetime.min.time())
        rows = await self._query_usage_trend_rows(
            provider,
            start_time,
            "DATE(timestamp)",
            "trend_bucket",
        )
        rows_by_bucket = {
            str(row["trend_bucket"]): dict(row)
            for row in rows
        }
        trend = []

        for offset in range(bucket_count):
            bucket_date = start_date + timedelta(days=offset)
            bucket_key = bucket_date.isoformat()
            trend.append(
                self._build_usage_trend_point(
                    row=rows_by_bucket.get(bucket_key, {}),
                    bucket=bucket_key,
                    label=bucket_date.strftime("%m-%d"),
                    tooltip_label=bucket_date.strftime("%Y-%m-%d"),
                )
            )

        return trend

    async def _query_usage_trend_rows(
        self,
        provider: Optional[str],
        start_time: datetime,
        bucket_expression: str,
        bucket_alias: str,
    ) -> list[aiosqlite.Row]:
        query = f"""
            SELECT
                {bucket_expression} as {bucket_alias},
                COUNT(*) as total_requests,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_requests,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(cache_creation_tokens) as cache_creation_tokens,
                SUM(cache_read_tokens) as cache_read_tokens
            FROM request_logs
            WHERE timestamp >= ?
        """
        params: List[object] = [_format_sqlite_datetime(start_time)]

        if provider:
            query += " AND provider = ?"
            params.append(provider)

        query += f" GROUP BY {bucket_expression} ORDER BY {bucket_alias} ASC"

        async with self.get_connection() as conn:
            cursor = await conn.execute(query, params)
            return await cursor.fetchall()

    def _build_usage_trend_point(
        self,
        *,
        row: Dict,
        bucket: str,
        label: str,
        tooltip_label: str,
    ) -> Dict:
        total_requests = int(row.get("total_requests") or 0)
        successful_requests = int(row.get("successful_requests") or 0)
        cache_creation_tokens = int(row.get("cache_creation_tokens") or 0)
        cache_read_tokens = int(row.get("cache_read_tokens") or 0)

        return {
            "bucket": bucket,
            "label": label,
            "tooltip_label": tooltip_label,
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": max(0, total_requests - successful_requests),
            "input_tokens": int(row.get("input_tokens") or 0),
            "output_tokens": int(row.get("output_tokens") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_total_tokens": (
                cache_creation_tokens + cache_read_tokens
            ),
            "success_rate": round(
                (
                    successful_requests / total_requests * 100
                ) if total_requests > 0 else 0,
                1,
            ),
        }

    async def get_model_stats_from_db(self, hours: int = 24) -> Dict:
        """
        从数据库获取模型统计（最近N小时）

        Args:
            hours: 小时数

        Returns:
            模型统计数据
        """
        start_time = datetime.utcnow() - timedelta(hours=hours)

        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    model,
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(total_tokens) as total_tokens,
                    AVG(duration) as avg_duration,
                    AVG(first_token_time) as avg_first_token_time
                FROM request_logs
                WHERE timestamp >= ?
                GROUP BY model
                ORDER BY total DESC
                """,
                (_format_sqlite_datetime(start_time),)
            )
            rows = await cursor.fetchall()

            result = {}
            for row in rows:
                model = row['model']
                result[model] = {
                    'total': row['total'],
                    'success': row['success'],
                    'failed': row['failed'],
                    'input_tokens': row['input_tokens'] or 0,
                    'output_tokens': row['output_tokens'] or 0,
                    'total_tokens': row['total_tokens'] or 0,
                    'avg_duration': round(row['avg_duration'] or 0, 2),
                    'avg_first_token_time': round(row['avg_first_token_time'] or 0, 2),
                    'success_rate': round(
                        (row['success'] / row['total'] * 100)
                        if row['total'] > 0
                        else 0,
                        1,
                    ),
                }

            return result

    async def delete_old_logs(self, days: int = 30) -> int:
        """
        删除旧日志

        Args:
            days: 保留天数

        Returns:
            删除的记录数
        """
        cutoff_time = datetime.utcnow() - timedelta(days=days)

        async with self.get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM request_logs WHERE timestamp < ?",
                (_format_sqlite_datetime(cutoff_time),)
            )
            await conn.commit()
            return cursor.rowcount


# 全局单例实例
_request_log_dao: Optional[RequestLogDAO] = None


def get_request_log_dao() -> RequestLogDAO:
    """
    获取请求日志 DAO 单例

    Returns:
        RequestLogDAO 实例
    """
    global _request_log_dao
    if _request_log_dao is None:
        _request_log_dao = RequestLogDAO()
    return _request_log_dao


def init_request_log_dao():
    """初始化请求日志 DAO"""
    global _request_log_dao
    _request_log_dao = RequestLogDAO()
    return _request_log_dao
