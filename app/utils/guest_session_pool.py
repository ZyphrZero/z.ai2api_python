#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""匿名访客会话池。"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional, Set

import httpx

from app.core.config import settings
from app.utils.fe_version import get_latest_fe_version
from app.utils.logger import logger
from app.utils.user_agent import get_random_user_agent

AUTH_URL = "https://chat.z.ai/api/v1/auths/"
CHATS_URL = "https://chat.z.ai/api/v1/chats/"
AUTH_HTTP_MAX_KEEPALIVE_CONNECTIONS = 20
AUTH_HTTP_MAX_CONNECTIONS = 50
GUEST_SESSION_TTL_SECONDS = 480
GUEST_SESSION_TTL_JITTER_SECONDS = 60
GUEST_SESSION_MIN_TTL_SECONDS = 180
GUEST_POOL_MAINTENANCE_INTERVAL_SECONDS = 30
GUEST_CLEANUP_PARALLELISM = 4
CAPACITY_FILL_ATTEMPT_MULTIPLIER = 3
CAPACITY_FILL_MIN_ATTEMPTS = 3
MAX_DUPLICATE_LOG_USER_IDS = 3


def _get_proxy_config() -> Optional[str]:
    """获取代理配置。"""
    if settings.HTTPS_PROXY:
        return settings.HTTPS_PROXY
    if settings.HTTP_PROXY:
        return settings.HTTP_PROXY
    if settings.SOCKS5_PROXY:
        return settings.SOCKS5_PROXY
    return None


def _build_timeout(read_timeout: float = 30.0) -> httpx.Timeout:
    """构建访客会话相关请求超时。"""
    return httpx.Timeout(
        connect=5.0,
        read=read_timeout,
        write=10.0,
        pool=5.0,
    )


def _build_limits() -> httpx.Limits:
    """构建访客会话相关连接池限制。"""
    return httpx.Limits(
        max_keepalive_connections=AUTH_HTTP_MAX_KEEPALIVE_CONNECTIONS,
        max_connections=AUTH_HTTP_MAX_CONNECTIONS,
    )


def _build_async_client(read_timeout: float = 30.0) -> httpx.AsyncClient:
    """构建访客会话相关 HTTP 客户端。"""
    return httpx.AsyncClient(
        timeout=_build_timeout(read_timeout),
        follow_redirects=True,
        limits=_build_limits(),
        proxy=_get_proxy_config(),
    )


def _build_dynamic_headers(chat_id: str = "") -> Dict[str, str]:
    """生成匿名访客鉴权所需浏览器请求头。"""
    browser_choices = [
        "chrome",
        "chrome",
        "chrome",
        "edge",
        "edge",
        "firefox",
        "safari",
    ]
    browser_type = random.choice(browser_choices)
    user_agent = get_random_user_agent(browser_type)
    fe_version = get_latest_fe_version()

    chrome_version = "139"
    edge_version = "139"

    if "Chrome/" in user_agent:
        try:
            chrome_version = user_agent.split("Chrome/")[1].split(".")[0]
        except Exception:
            pass

    if "Edg/" in user_agent:
        try:
            edge_version = user_agent.split("Edg/")[1].split(".")[0]
            sec_ch_ua = (
                f'"Microsoft Edge";v="{edge_version}", '
                f'"Chromium";v="{chrome_version}", "Not_A Brand";v="24"'
            )
        except Exception:
            sec_ch_ua = (
                f'"Not_A Brand";v="8", "Chromium";v="{chrome_version}", '
                f'"Google Chrome";v="{chrome_version}"'
            )
    elif "Firefox/" in user_agent:
        sec_ch_ua = None
    else:
        sec_ch_ua = (
            f'"Not_A Brand";v="8", "Chromium";v="{chrome_version}", '
            f'"Google Chrome";v="{chrome_version}"'
        )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "User-Agent": user_agent,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-FE-Version": fe_version,
        "Origin": "https://chat.z.ai",
    }

    if sec_ch_ua:
        headers["sec-ch-ua"] = sec_ch_ua
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = '"Windows"'

    if chat_id:
        headers["Referer"] = f"https://chat.z.ai/c/{chat_id}"
    else:
        headers["Referer"] = "https://chat.z.ai/"

    return headers


def _build_session_expiry() -> float:
    """为新会话分配带抖动的过期时间，避免整池同时失效。"""
    jitter = random.uniform(
        -GUEST_SESSION_TTL_JITTER_SECONDS,
        GUEST_SESSION_TTL_JITTER_SECONDS,
    )
    ttl_seconds = max(
        GUEST_SESSION_MIN_TTL_SECONDS,
        GUEST_SESSION_TTL_SECONDS + jitter,
    )
    return time.time() + ttl_seconds


@dataclass
class GuestSession:
    """单个匿名访客会话。"""

    token: str
    user_id: str
    username: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=_build_session_expiry)
    active_requests: int = 0
    valid: bool = True
    failure_count: int = 0
    last_failure_time: float = 0.0

    @property
    def age(self) -> float:
        """会话存活时间。"""
        return time.time() - self.created_at

    @property
    def is_expired(self) -> bool:
        """判断会话是否已过期。"""
        return time.time() >= self.expires_at

    def snapshot(self) -> Dict[str, str]:
        """获取当前会话快照。"""
        return {
            "token": self.token,
            "user_id": self.user_id,
            "username": self.username,
        }


class GuestSessionPool:
    """匿名访客会话池，支持最小负载获取与失败替换。"""

    def __init__(self, pool_size: int = 3):
        self.pool_size = max(1, pool_size)
        self._lock = Lock()
        self._sessions: Dict[str, GuestSession] = {}
        self._maintenance_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()
        self._capacity_lock = asyncio.Lock()
        self._background_tasks: Set[asyncio.Task] = set()
        self._cleanup_parallelism = GUEST_CLEANUP_PARALLELISM
        self._maintenance_interval = GUEST_POOL_MAINTENANCE_INTERVAL_SECONDS

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取可复用的 HTTP 客户端，减少频繁建连开销。"""
        if self._http_client is not None:
            return self._http_client

        async with self._client_lock:
            if self._http_client is None:
                self._http_client = _build_async_client()
        return self._http_client

    async def _close_http_client(self):
        """关闭可复用的 HTTP 客户端。"""
        async with self._client_lock:
            client = self._http_client
            self._http_client = None

        if client is not None:
            await client.aclose()

    def _track_background_task(self, coro) -> asyncio.Task:
        """跟踪后台任务，避免清理阻塞前台重试路径。"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _on_done(done_task: asyncio.Task):
            self._background_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"⚠️ 匿名会话后台任务异常: {exc}")

        task.add_done_callback(_on_done)
        return task

    async def _wait_background_tasks(self):
        """等待当前已注册的后台任务结束。"""
        pending = list(self._background_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _delete_sessions_concurrently(self, sessions: List[GuestSession]):
        """并发清理多枚匿名会话，加快池维护速度。"""
        if not sessions:
            return

        semaphore = asyncio.Semaphore(self._cleanup_parallelism)

        async def _cleanup(session: GuestSession):
            async with semaphore:
                await self._delete_all_chats(session)

        await asyncio.gather(*(_cleanup(session) for session in sessions))

    async def _create_session(self) -> GuestSession:
        """创建一个新的匿名访客会话。"""
        headers = _build_dynamic_headers()

        # 访客鉴权会写入 cookie，复用同一个 client 会把“新建会话”粘回旧访客身份。
        async with _build_async_client() as auth_client:
            response = await auth_client.get(AUTH_URL, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(
                f"匿名会话创建失败: HTTP {response.status_code} {response.text[:200]}"
            )

        data = response.json()
        token = str(data.get("token") or "").strip()
        user_id = str(
            data.get("id") or data.get("user_id") or data.get("uid") or ""
        ).strip()
        username = str(
            data.get("name")
            or str(data.get("email") or "").split("@")[0]
            or f"guest-{user_id[:8] or 'session'}"
        ).strip()

        if not token:
            raise RuntimeError(f"匿名会话创建失败: 未返回 token {data}")
        if not user_id:
            user_id = f"guest-{token[:12]}"

        logger.info(
            f"🫥 创建匿名会话成功: user_id={user_id}, username={username or 'Guest'}"
        )
        return GuestSession(
            token=token,
            user_id=user_id,
            username=username or "Guest",
        )

    async def _delete_all_chats(self, session: GuestSession) -> bool:
        """删除匿名会话的全部对话，尽量释放并发占用。"""
        headers = _build_dynamic_headers()
        headers.update(
            {
                "Authorization": f"Bearer {session.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        try:
            client = await self._get_http_client()
            response = await client.delete(CHATS_URL, headers=headers)

            if response.status_code == 200:
                logger.info(f"🧹 已清理匿名会话聊天记录: {session.user_id}")
                return True

            logger.warning(
                f"⚠️ 清理匿名会话聊天记录失败: {session.user_id}, "
                f"HTTP {response.status_code}, body={response.text[:200]}"
            )
        except Exception as exc:
            logger.warning(f"⚠️ 清理匿名会话聊天记录异常: {session.user_id}, {exc}")

        return False

    def _list_valid_sessions(
        self,
        exclude_user_ids: Optional[Set[str]] = None,
    ) -> List[GuestSession]:
        """获取有效匿名会话列表。"""
        excluded = exclude_user_ids or set()
        with self._lock:
            return [
                session
                for session in self._sessions.values()
                if self._is_session_usable(session)
                and session.user_id not in excluded
            ]

    def _is_session_usable(self, session: GuestSession) -> bool:
        """判断会话当前是否还能继续分配。"""
        return session.valid and not session.is_expired

    def _should_retire_session(self, session: GuestSession) -> bool:
        """判断会话是否应当从池中回收。"""
        return session.active_requests == 0 and not self._is_session_usable(session)

    def _can_replace_session(self, session: GuestSession) -> bool:
        """判断当前池内会话是否允许被新的同 user_id 会话替换。"""
        return self._should_retire_session(session)

    def _store_session(self, session: GuestSession) -> bool:
        """仅在会话唯一或旧会话已过期时写入会话池。"""
        with self._lock:
            existing = self._sessions.get(session.user_id)
            if existing and not self._can_replace_session(existing):
                return False
            self._sessions[session.user_id] = session
            return True

    def _log_duplicate_sessions(self, action: str, user_ids: List[str]):
        """记录重复会话，避免补池时静默覆盖。"""
        if not user_ids:
            return

        sample = ", ".join(user_ids[:MAX_DUPLICATE_LOG_USER_IDS])
        logger.warning(
            f"⚠️ 匿名会话池{action}收到重复会话，已忽略: "
            f"count={len(user_ids)}, user_ids={sample}"
        )

    def _register_create_results(self, action: str, results: List[object]) -> int:
        """写入新创建的会话，并显式忽略重复 user_id。"""
        created = 0
        duplicate_user_ids: List[str] = []

        for result in results:
            if isinstance(result, GuestSession):
                if self._store_session(result):
                    created += 1
                else:
                    duplicate_user_ids.append(result.user_id)
                continue

            if isinstance(result, Exception):
                logger.warning(f"⚠️ 匿名会话池{action}失败: {result}")

        self._log_duplicate_sessions(action, duplicate_user_ids)
        return created

    def _get_fill_attempt_budget(self, missing_count: int) -> int:
        """为补池/获取会话计算显式尝试上限，避免重复会话导致死循环。"""
        scaled_budget = max(1, missing_count) * CAPACITY_FILL_ATTEMPT_MULTIPLIER
        minimum_budget = max(1, missing_count) + CAPACITY_FILL_MIN_ATTEMPTS
        return max(scaled_budget, minimum_budget)

    def _pop_retired_sessions(self) -> List[GuestSession]:
        """移除当前所有可回收的失效会话。"""
        retired_sessions: List[GuestSession] = []

        with self._lock:
            for user_id, session in list(self._sessions.items()):
                if self._should_retire_session(session):
                    retired_sessions.append(self._sessions.pop(user_id))

        return retired_sessions

    async def _ensure_capacity(self):
        """补齐匿名会话池容量。"""
        async with self._capacity_lock:
            attempts_left = self._get_fill_attempt_budget(
                self.pool_size - len(self._list_valid_sessions())
            )

            while attempts_left > 0:
                need = self.pool_size - len(self._list_valid_sessions())
                if need <= 0:
                    return

                batch_size = min(need, attempts_left)
                results = await asyncio.gather(
                    *[self._create_session() for _ in range(batch_size)],
                    return_exceptions=True,
                )
                attempts_left -= batch_size

                created = self._register_create_results("补齐", results)
                if created == 0 and attempts_left == 0:
                    break

            remaining = self.pool_size - len(self._list_valid_sessions())
            if remaining > 0:
                logger.warning(
                    "⚠️ 匿名会话池补齐未达到目标容量: "
                    f"missing={remaining}, current={len(self._list_valid_sessions())}"
                )

    async def _maintenance_loop(self):
        """后台维护：回收过期/失效会话，并补齐池容量。"""
        while True:
            try:
                await asyncio.sleep(self._maintenance_interval)
                retired_sessions = self._pop_retired_sessions()
                await self._delete_sessions_concurrently(retired_sessions)

                await self._ensure_capacity()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(f"⚠️ 匿名会话池后台维护异常: {exc}")

    async def initialize(self):
        """初始化匿名会话池。"""
        if self._maintenance_task:
            return

        await self._ensure_capacity()
        created = len(self._list_valid_sessions())

        if created == 0:
            fallback = await self._create_session()
            if not self._store_session(fallback):
                raise RuntimeError(
                    "匿名会话池初始化失败: 无法写入唯一匿名会话"
                )
            created = len(self._list_valid_sessions())

        logger.info(f"✅ 匿名会话池初始化完成: {created} 个会话")
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def close(self):
        """关闭匿名会话池。"""
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
            self._maintenance_task = None

        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        await self._wait_background_tasks()
        idle_sessions = [
            session for session in sessions if session.active_requests == 0
        ]
        await self._delete_sessions_concurrently(idle_sessions)
        await self._close_http_client()

    async def acquire(
        self,
        exclude_user_ids: Optional[Set[str]] = None,
    ) -> GuestSession:
        """按最小忙碌度获取一个可用匿名会话。"""
        excluded = exclude_user_ids or set()
        attempts_left = self._get_fill_attempt_budget(len(excluded) + 1)

        while attempts_left > 0:
            candidates = self._list_valid_sessions(exclude_user_ids=excluded)
            if candidates:
                session = min(
                    candidates,
                    key=lambda item: (item.active_requests, item.created_at),
                )
                with self._lock:
                    current = self._sessions.get(session.user_id)
                    if (
                        current
                        and self._is_session_usable(current)
                        and current.user_id not in excluded
                    ):
                        current.active_requests += 1
                        return current

            new_session = await self._create_session()
            attempts_left -= 1
            if new_session.user_id in excluded:
                logger.warning(
                    "⚠️ 获取匿名会话时命中排除 user_id，已忽略: "
                    f"{new_session.user_id}"
                )
                continue

            if not self._store_session(new_session):
                logger.warning(
                    "⚠️ 获取匿名会话时命中重复 user_id，已重试: "
                    f"{new_session.user_id}"
                )
                continue

            with self._lock:
                current = self._sessions.get(new_session.user_id)
                if current and self._is_session_usable(current):
                    current.active_requests += 1
                    return current

        raise RuntimeError("匿名会话池获取失败: 未能创建唯一匿名会话")

    def release(self, user_id: str):
        """释放一个匿名会话占用。"""
        retired_session: Optional[GuestSession] = None

        with self._lock:
            session = self._sessions.get(user_id)
            if session:
                session.active_requests = max(0, session.active_requests - 1)
                if self._should_retire_session(session):
                    retired_session = self._sessions.pop(user_id)

        if retired_session:
            logger.info(f"🧹 已回收过期匿名会话: {retired_session.user_id}")
            self._track_background_task(self._delete_all_chats(retired_session))
            self._track_background_task(self._ensure_capacity())

    async def report_failure(self, user_id: Optional[str] = None):
        """标记匿名会话失效，并尝试补一个新会话。"""
        session: Optional[GuestSession] = None

        if user_id:
            with self._lock:
                session = self._sessions.pop(user_id, None)
                if session:
                    session.valid = False
                    session.failure_count += 1
                    session.last_failure_time = time.time()
                    session.active_requests = 0

        if session:
            self._track_background_task(self._delete_all_chats(session))
            logger.warning(f"⚠️ 已淘汰匿名会话: {session.user_id}")

        await self._ensure_capacity()

    async def refresh_auth(self, failed_user_id: Optional[str] = None):
        """兼容 glm-demo 命名：刷新匿名会话。"""
        await self.report_failure(failed_user_id)

    async def cleanup_idle_chats(self):
        """清理当前空闲匿名会话的聊天记录。"""
        with self._lock:
            idle_sessions = [
                session
                for session in self._sessions.values()
                if self._is_session_usable(session) and session.active_requests == 0
            ]

        await self._delete_sessions_concurrently(idle_sessions)

    def get_pool_status(self) -> Dict[str, int]:
        """获取匿名会话池状态。"""
        with self._lock:
            sessions = list(self._sessions.values())

        valid_sessions = [
            session for session in sessions if self._is_session_usable(session)
        ]
        busy_sessions = [
            session for session in valid_sessions if session.active_requests > 0
        ]

        return {
            "total_sessions": len(sessions),
            "valid_sessions": len(valid_sessions),
            "available_sessions": len(
                [session for session in valid_sessions if session.active_requests == 0]
            ),
            "busy_sessions": len(busy_sessions),
            "expired_sessions": len(
                [session for session in sessions if session.is_expired]
            ),
        }


_guest_session_pool: Optional[GuestSessionPool] = None
_guest_pool_lock = Lock()


def get_guest_session_pool() -> Optional[GuestSessionPool]:
    """获取全局匿名会话池。"""
    return _guest_session_pool


async def initialize_guest_session_pool(
    pool_size: int = 3,
) -> GuestSessionPool:
    """初始化全局匿名会话池。"""
    global _guest_session_pool

    with _guest_pool_lock:
        if _guest_session_pool is None:
            _guest_session_pool = GuestSessionPool(pool_size=pool_size)
        pool = _guest_session_pool

    await pool.initialize()
    return pool


async def close_guest_session_pool():
    """关闭全局匿名会话池。"""
    global _guest_session_pool

    with _guest_pool_lock:
        pool = _guest_session_pool
        _guest_session_pool = None

    if pool:
        await pool.close()
