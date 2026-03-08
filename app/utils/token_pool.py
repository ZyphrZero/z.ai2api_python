#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Token 池管理器 - 基于数据库的 Token 轮询和健康检查

核心功能：
1. Token 轮询机制 - 负载均衡和容错
2. Z.AI 官方认证接口验证 - 基于 role 字段区分用户类型
3. Token 健康度监控 - 自动禁用失败 Token
4. 数据库集成 - 与 TokenDAO 协同工作
"""

import asyncio
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from threading import Lock
import httpx

from app.utils.logger import logger


# ==================== Token 状态管理 ====================


@dataclass
class TokenStatus:
    """Token 运行时状态（内存中）"""
    token: str
    token_id: int  # 数据库 ID，用于同步统计
    token_type: str = "unknown"  # "user", "guest", "unknown"
    is_available: bool = True
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    total_requests: int = 0
    successful_requests: int = 0

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests

    @property
    def is_healthy(self) -> bool:
        """
        Token 健康状态判断

        健康标准：
        1. 必须是认证用户 Token (token_type = "user")
        2. 当前可用 (is_available = True)
        3. 成功率 >= 50% 或总请求数 <= 3（新 Token 容错）

        注意：
        - guest Token 永远不健康
        - unknown Token 永远不健康
        """
        # guest 和 unknown token 永远不健康
        if self.token_type != "user":
            return False

        # 不可用的 token 不健康
        if not self.is_available:
            return False

        # 新 token 容错：请求数很少时，只要没失败就健康
        if self.total_requests <= 3:
            return self.failure_count == 0

        # 基于成功率判断
        return self.success_rate >= 0.5


# ==================== Token 验证服务 ====================


class ZAITokenValidator:
    """Z.AI Token 验证器（使用官方认证接口）"""

    AUTH_URL = "https://chat.z.ai/api/v1/auths/"

    @staticmethod
    def get_headers(token: str) -> Dict[str, str]:
        """构建认证请求头"""
        return {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Authorization": f"Bearer {token}",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "DNT": "1",
            "Referer": "https://chat.z.ai/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"'
        }

    @classmethod
    async def validate_token(cls, token: str) -> Tuple[str, bool, Optional[str]]:
        """
        验证 Token 有效性并返回类型

        Args:
            token: 待验证的 Token

        Returns:
            (token_type, is_valid, error_message)
            - token_type: "user" | "guest" | "unknown"
            - is_valid: True 表示是有效的认证用户 Token
            - error_message: 失败原因（仅在 is_valid=False 时有值）
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    cls.AUTH_URL,
                    headers=cls.get_headers(token)
                )

                # 解析响应
                return cls._parse_auth_response(response)

        except httpx.TimeoutException:
            return ("unknown", False, "请求超时")
        except httpx.ConnectError:
            return ("unknown", False, "连接失败")
        except Exception as e:
            return ("unknown", False, f"验证异常: {str(e)}")

    @staticmethod
    def _parse_auth_response(response: httpx.Response) -> Tuple[str, bool, Optional[str]]:
        """
        解析 Z.AI 认证接口响应

        响应格式示例：
        {
            "id": "...",
            "email": "user@example.com",
            "role": "user"  # 或 "guest"
        }

        验证规则：
        - role: "user" → 认证用户 Token（有效，可添加）
        - role: "guest" → 匿名用户 Token（无效，拒绝添加）
        - 其他情况 → 无效 Token
        """
        # 检查 HTTP 状态码
        if response.status_code != 200:
            return ("unknown", False, f"HTTP {response.status_code}")

        try:
            data = response.json()

            # 验证响应格式
            if not isinstance(data, dict):
                return ("unknown", False, "无效的响应格式")

            # 检查是否包含错误信息
            if "error" in data or "message" in data:
                error_msg = data.get("error") or data.get("message", "未知错误")
                return ("unknown", False, str(error_msg))

            # 核心验证：检查 role 字段
            role = data.get("role")

            if role == "user":
                return ("user", True, None)
            elif role == "guest":
                return ("guest", False, "匿名用户 Token 不允许添加")
            else:
                return ("unknown", False, f"未知 role: {role}")

        except (ValueError, Exception) as e:
            return ("unknown", False, f"解析响应失败: {str(e)}")


# ==================== Token 池管理器 ====================


class TokenPool:
    """Token 池管理器（数据库驱动）"""

    def __init__(
        self,
        tokens: List[Tuple[int, str, str]],  # [(token_id, token_value, token_type), ...]
        failure_threshold: int = 3,
        recovery_timeout: int = 1800
    ):
        """
        初始化 Token 池

        Args:
            tokens: Token 列表 [(token_id, token_value, token_type), ...]
            failure_threshold: 失败阈值，超过此次数将标记为不可用
            recovery_timeout: 恢复超时时间（秒），失败 Token 在此时间后重新尝试
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._lock = Lock()
        self._current_index = 0

        # 初始化 Token 状态（内存中）
        self.token_statuses: Dict[str, TokenStatus] = {}
        self.token_id_map: Dict[str, int] = {}  # token -> token_id 映射

        for token_id, token_value, token_type in tokens:
            if token_value and token_value not in self.token_statuses:
                self.token_statuses[token_value] = TokenStatus(
                    token=token_value,
                    token_id=token_id,
                    token_type=token_type
                )
                self.token_id_map[token_value] = token_id

        if not self.token_statuses:
            logger.warning("⚠️ Token 池为空，将依赖匿名模式")

    def get_next_token(self, exclude_tokens: Optional[Set[str]] = None) -> Optional[str]:
        """
        获取下一个可用的认证用户 Token（轮询算法）

        Returns:
            可用的 Token 字符串，如果没有可用 Token 则返回 None
        """
        with self._lock:
            if not self.token_statuses:
                return None

            excluded = exclude_tokens or set()

            available_tokens = self._get_available_user_tokens()
            if excluded:
                available_tokens = [
                    token for token in available_tokens if token not in excluded
                ]
            if not available_tokens:
                # 尝试恢复过期的失败 Token
                self._try_recover_failed_tokens()
                available_tokens = self._get_available_user_tokens()
                if excluded:
                    available_tokens = [
                        token for token in available_tokens if token not in excluded
                    ]

                if not available_tokens:
                    logger.warning("⚠️ 没有可用的认证用户 Token")
                    return None

            # 轮询选择
            token = available_tokens[self._current_index % len(available_tokens)]
            self._current_index = (self._current_index + 1) % len(available_tokens)

            return token

    def _get_available_user_tokens(self) -> List[str]:
        """
        获取当前可用的认证用户 Token 列表

        过滤条件：
        1. is_available = True
        2. token_type == "user"
        """
        available_user_tokens = [
            status.token for status in self.token_statuses.values()
            if status.is_available and status.token_type == "user"
        ]

        # 警告：如果有 guest token 但没有 user token
        if not available_user_tokens and self.token_statuses:
            guest_count = sum(
                1 for status in self.token_statuses.values()
                if status.token_type == "guest"
            )
            if guest_count > 0:
                logger.warning(f"⚠️ 检测到 {guest_count} 个匿名用户 Token，轮询机制将跳过这些 Token")

        return available_user_tokens

    def _try_recover_failed_tokens(self):
        """尝试恢复失败的 Token（仅针对认证用户 Token）"""
        current_time = time.time()
        recovered_count = 0

        for status in self.token_statuses.values():
            # 只恢复认证用户 Token
            if (
                status.token_type == "user"
                and not status.is_available
                and current_time - status.last_failure_time > self.recovery_timeout
            ):
                status.is_available = True
                status.failure_count = 0
                recovered_count += 1
                logger.info(f"🔄 恢复失败 Token: {status.token[:20]}...")

        if recovered_count > 0:
            logger.info(f"✅ 恢复了 {recovered_count} 个失败的 Token")

    def mark_token_success(self, token: str):
        """标记 Token 使用成功"""
        with self._lock:
            if token in self.token_statuses:
                status = self.token_statuses[token]
                status.total_requests += 1
                status.successful_requests += 1
                status.last_success_time = time.time()
                status.failure_count = 0  # 重置失败计数

                if not status.is_available:
                    status.is_available = True
                    logger.info(f"✅ Token 恢复可用: {token[:20]}...")

    def mark_token_failure(self, token: str, error: Exception = None):
        """标记 Token 使用失败"""
        with self._lock:
            if token in self.token_statuses:
                status = self.token_statuses[token]
                status.total_requests += 1
                status.failure_count += 1
                status.last_failure_time = time.time()

                if status.failure_count >= self.failure_threshold:
                    status.is_available = False
                    logger.warning(f"🚫 Token 已禁用: {token[:20]}... (失败 {status.failure_count} 次)")

    def get_token_id(self, token: str) -> Optional[int]:
        """获取 Token 的数据库 ID"""
        return self.token_id_map.get(token)

    def get_pool_status(self) -> Dict:
        """获取 Token 池状态信息"""
        with self._lock:
            available_count = len(self._get_available_user_tokens())
            total_count = len(self.token_statuses)
            healthy_count = sum(1 for status in self.token_statuses.values() if status.is_healthy)

            # 统计各类型 Token
            user_count = sum(1 for s in self.token_statuses.values() if s.token_type == "user")
            guest_count = sum(1 for s in self.token_statuses.values() if s.token_type == "guest")
            unknown_count = sum(1 for s in self.token_statuses.values() if s.token_type == "unknown")

            status_info = {
                "total_tokens": total_count,
                "available_tokens": available_count,
                "unavailable_tokens": total_count - available_count,
                "healthy_tokens": healthy_count,
                "unhealthy_tokens": total_count - healthy_count,
                "user_tokens": user_count,
                "guest_tokens": guest_count,
                "unknown_tokens": unknown_count,
                "current_index": self._current_index,
                "tokens": []
            }

            for token, status in self.token_statuses.items():
                status_info["tokens"].append({
                    "token": f"{token[:10]}...{token[-10:]}",
                    "token_id": status.token_id,
                    "token_type": status.token_type,
                    "is_available": status.is_available,
                    "failure_count": status.failure_count,
                    "success_count": status.successful_requests,
                    "success_rate": f"{status.success_rate:.2%}",
                    "total_requests": status.total_requests,
                    "is_healthy": status.is_healthy,
                    "last_failure_time": status.last_failure_time,
                    "last_success_time": status.last_success_time
                })

            return status_info

    def update_token_type(self, token: str, token_type: str):
        """更新 Token 类型（用于健康检查后更新）"""
        with self._lock:
            if token in self.token_statuses:
                old_type = self.token_statuses[token].token_type
                self.token_statuses[token].token_type = token_type

                if old_type != token_type:
                    logger.info(f"🔄 更新 Token 类型: {token[:20]}... {old_type} → {token_type}")

    async def health_check_token(self, token: str) -> bool:
        """
        异步健康检查单个 Token（使用 Z.AI 官方认证接口）

        Args:
            token: 要检查的 Token

        Returns:
            Token 是否健康（True = 有效的认证用户 Token）
        """
        token_type, is_valid, error_message = await ZAITokenValidator.validate_token(token)

        # 更新 Token 类型
        self.update_token_type(token, token_type)

        # 更新状态
        if is_valid:
            self.mark_token_success(token)
        else:
            self.mark_token_failure(token, Exception(error_message or "验证失败"))

        return is_valid

    async def health_check_all(self):
        """异步健康检查所有 Token"""
        if not self.token_statuses:
            logger.warning("⚠️ Token 池为空，跳过健康检查")
            return

        total_tokens = len(self.token_statuses)
        logger.info(f"🔍 开始 Token 池健康检查... (共 {total_tokens} 个 Token)")

        # 并发执行所有 Token 的健康检查
        tasks = [
            self.health_check_token(token)
            for token in self.token_statuses.keys()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 统计结果
        healthy_count = sum(1 for r in results if r is True)
        failed_count = sum(1 for r in results if r is False)
        exception_count = sum(1 for r in results if isinstance(r, Exception))

        health_rate = (healthy_count / total_tokens) * 100 if total_tokens > 0 else 0

        if healthy_count == 0 and total_tokens > 0:
            logger.warning(f"⚠️ 健康检查完成: 0/{total_tokens} 个 Token 健康 - 请检查 Token 配置")
        elif failed_count > 0:
            logger.warning(f"⚠️ 健康检查完成: {healthy_count}/{total_tokens} 个 Token 健康 ({health_rate:.1f}%)")
        else:
            logger.info(f"✅ 健康检查完成: {healthy_count}/{total_tokens} 个 Token 健康")

        if exception_count > 0:
            logger.error(f"💥 {exception_count} 个 Token 检查异常")

    async def sync_from_database(self, provider: str = "zai"):
        """
        从数据库同步 Token 状态（禁用/启用状态）

        Args:
            provider: 提供商名称

        说明：
            - 从数据库读取最新的 Token 启用状态
            - 如果数据库中 Token 被禁用，则从池中移除
            - 如果数据库中有新增的启用 Token，则添加到池中
            - 保留现有 Token 的运行时统计（请求数、成功率等）
        """
        from app.services.token_dao import get_token_dao

        dao = get_token_dao()

        # 从数据库加载所有启用的认证用户 Token
        token_records = await dao.get_tokens_by_provider(provider, enabled_only=True)

        # 构建数据库中的 Token 映射
        db_tokens = {
            record["token"]: (record["id"], record.get("token_type", "unknown"))
            for record in token_records
            if record.get("token_type") != "guest"  # 过滤 guest token
        }

        with self._lock:
            # 1. 移除已在数据库中禁用的 Token
            tokens_to_remove = []
            for token_value in list(self.token_statuses.keys()):
                if token_value not in db_tokens:
                    tokens_to_remove.append(token_value)

            for token_value in tokens_to_remove:
                del self.token_statuses[token_value]
                del self.token_id_map[token_value]
                logger.info(f"🗑️ 从池中移除已禁用 Token: {token_value[:20]}...")

            # 2. 添加新启用的 Token
            new_tokens_count = 0
            for token_value, (token_id, token_type) in db_tokens.items():
                if token_value not in self.token_statuses:
                    self.token_statuses[token_value] = TokenStatus(
                        token=token_value,
                        token_id=token_id,
                        token_type=token_type
                    )
                    self.token_id_map[token_value] = token_id
                    new_tokens_count += 1
                    logger.info(f"➕ 添加新启用 Token: {token_value[:20]}...")

            # 3. 更新现有 Token 的类型（如果数据库中有更新）
            for token_value, (token_id, token_type) in db_tokens.items():
                if token_value in self.token_statuses:
                    old_type = self.token_statuses[token_value].token_type
                    if old_type != token_type:
                        self.token_statuses[token_value].token_type = token_type
                        logger.info(f"🔄 更新 Token 类型: {token_value[:20]}... {old_type} → {token_type}")

            logger.info(
                f"✅ Token 池同步完成: "
                f"当前 {len(self.token_statuses)} 个 Token "
                f"(移除 {len(tokens_to_remove)}, 新增 {new_tokens_count})"
            )


# ==================== 全局实例管理 ====================


_token_pool: Optional[TokenPool] = None
_pool_lock = Lock()


def get_token_pool() -> Optional[TokenPool]:
    """获取全局 Token 池实例"""
    return _token_pool


async def initialize_token_pool_from_db(
    provider: str = "zai",
    failure_threshold: int = 3,
    recovery_timeout: int = 1800
) -> Optional[TokenPool]:
    """
    从数据库初始化全局 Token 池

    Args:
        provider: 提供商名称（当前仅使用 zai）
        failure_threshold: 失败阈值
        recovery_timeout: 恢复超时时间（秒）

    Returns:
        TokenPool 实例（即使没有 Token 也会创建空池）
    """
    global _token_pool

    from app.services.token_dao import get_token_dao

    dao = get_token_dao()

    # 从数据库加载 Token（只加载启用的认证用户 Token）
    token_records = await dao.get_tokens_by_provider(provider, enabled_only=True)

    # 转换为 TokenPool 所需格式
    tokens = []
    if token_records:
        tokens = [
            (record["id"], record["token"], record.get("token_type", "unknown"))
            for record in token_records
        ]

        # 过滤掉 guest token（不应该在数据库中，但防御性检查）
        user_tokens = [
            (tid, tval, ttype) for tid, tval, ttype in tokens
            if ttype != "guest"
        ]

        if len(user_tokens) < len(tokens):
            guest_count = len(tokens) - len(user_tokens)
            logger.warning(f"⚠️ 过滤了 {guest_count} 个匿名用户 Token")

        tokens = user_tokens

    # 始终创建 Token 池实例（即使为空）
    with _pool_lock:
        _token_pool = TokenPool(tokens, failure_threshold, recovery_timeout)

        if not tokens:
            logger.warning(f"⚠️ {provider} 没有有效的认证用户 Token，已创建空 Token 池")
        else:
            logger.info(f"🔧 从数据库初始化 Token 池（{provider}），共 {len(tokens)} 个 Token")

        return _token_pool


async def sync_token_stats_to_db():
    """
    将内存中的 Token 统计同步到数据库

    应在服务关闭或定期调用，确保统计数据不丢失
    """
    pool = get_token_pool()
    if not pool:
        return

    from app.services.token_dao import get_token_dao

    dao = get_token_dao()

    with pool._lock:
        for token, status in pool.token_statuses.items():
            token_id = status.token_id

            # 更新数据库统计（简化版，实际可能需要增量更新）
            if status.successful_requests > 0:
                for _ in range(status.successful_requests):
                    await dao.record_success(token_id)

            if status.total_requests - status.successful_requests > 0:
                for _ in range(status.total_requests - status.successful_requests):
                    await dao.record_failure(token_id)

    logger.info("✅ Token 统计已同步到数据库")
