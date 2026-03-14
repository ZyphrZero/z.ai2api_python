#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""上游适配器。"""

import asyncio
import base64
import json
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.openai_compat import (
    create_openai_chunk,
    create_openai_response_with_reasoning,
    format_sse_chunk,
    handle_error,
)
from app.models.schemas import OpenAIRequest
from app.utils.fe_version import get_latest_fe_version
from app.utils.guest_session_pool import get_guest_session_pool
from app.utils.logger import get_logger
from app.utils.signature import generate_signature
from app.utils.token_pool import get_token_pool
from app.utils.tool_call_handler import (
    parse_and_extract_tool_calls,
)
from app.utils.user_agent import get_random_user_agent

logger = get_logger()

DEFAULT_ZAI_BASE_URL = "https://chat.z.ai"
CHAT_BOOTSTRAP_MAX_CONTENT_LEN = 500
DEFAULT_PLATFORM = "web"
DEFAULT_CLIENT_VERSION = "0.0.1"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_SCREEN_WIDTH = "1920"
DEFAULT_SCREEN_HEIGHT = "1080"
DEFAULT_VIEWPORT_WIDTH = "944"
DEFAULT_VIEWPORT_HEIGHT = "919"
DEFAULT_VIEWPORT_SIZE = f"{DEFAULT_VIEWPORT_WIDTH}x{DEFAULT_VIEWPORT_HEIGHT}"
DEFAULT_SCREEN_RESOLUTION = f"{DEFAULT_SCREEN_WIDTH}x{DEFAULT_SCREEN_HEIGHT}"
DEFAULT_COLOR_DEPTH = "24"
DEFAULT_PIXEL_RATIO = "1.25"
DEFAULT_MAX_TOUCH_POINTS = "10"
DEFAULT_TIMEZONE_OFFSET = "-480"
DEFAULT_PAGE_TITLE = "Z.ai Chat Proxy"
DEFAULT_COMPLETION_FEATURES = [
    {"type": "mcp", "server": "vibe-coding", "status": "hidden"},
    {"type": "mcp", "server": "ppt-maker", "status": "hidden"},
    {"type": "mcp", "server": "image-search", "status": "hidden"},
    {"type": "mcp", "server": "deep-research", "status": "hidden"},
    {"type": "tool_selector", "server": "tool_selector", "status": "hidden"},
    {"type": "mcp", "server": "advanced-search", "status": "hidden"},
]
GLM46V_MCP_SERVERS = [
    "vlm-image-search",
    "vlm-image-recognition",
    "vlm-image-processing",
]
GLM46V_SELECTED_FEATURES = [
    {"type": "mcp", "server": "vlm-image-search", "status": "selected"},
    {"type": "mcp", "server": "vlm-image-recognition", "status": "selected"},
    {"type": "mcp", "server": "vlm-image-processing", "status": "selected"},
]

def generate_uuid() -> str:
    """生成UUID v4"""
    return str(uuid.uuid4())

def get_dynamic_headers(
    chat_id: str = "",
    browser_type: Optional[str] = None,
) -> Dict[str, str]:
    """生成上游请求所需的动态浏览器 headers。"""
    browser_choices = [
        "chrome",
        "chrome",
        "chrome",
        "edge",
        "edge",
        "firefox",
        "safari",
    ]
    selected_browser = browser_type or random.choice(browser_choices)
    user_agent = get_random_user_agent(selected_browser)
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

def _urlsafe_b64decode(data: str) -> bytes:
    """Decode a URL-safe base64 string with proper padding."""
    if isinstance(data, str):
        data_bytes = data.encode("utf-8")
    else:
        data_bytes = data
    padding = b"=" * (-len(data_bytes) % 4)
    return base64.urlsafe_b64decode(data_bytes + padding)


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    """Decode JWT payload without verification to extract metadata."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_raw = _urlsafe_b64decode(parts[1])
        return json.loads(payload_raw.decode("utf-8", errors="ignore"))
    except Exception:
        return {}


def _extract_user_id_from_token(token: str) -> str:
    """Extract user_id from a JWT's payload. Fallback to 'guest'."""
    payload = _decode_jwt_payload(token) if token else {}
    for key in ("id", "user_id", "uid", "sub"):
        val = payload.get(key)
        if isinstance(val, (str, int)) and str(val):
            return str(val)
    return "guest"


def _extract_text_from_content(content: Any) -> str:
    """Extract text parts from OpenAI-compatible content payloads."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return " ".join(part for part in parts if part).strip()

    if content is None:
        return ""

    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _stringify_tool_arguments(arguments: Any) -> str:
    """Normalize tool-call arguments into a JSON string."""
    if isinstance(arguments, str):
        return arguments

    try:
        return json.dumps(arguments or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def _build_tool_call_index(
    messages: List[Dict[str, Any]],
) -> Dict[str, Dict[str, str]]:
    """Index assistant tool calls by id for later tool-result messages."""
    index: Dict[str, Dict[str, str]] = {}

    for message in messages:
        if message.get("role") != "assistant":
            continue

        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue

        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue

            tool_call_id = tool_call.get("id")
            function_data = (
                tool_call.get("function")
                if isinstance(tool_call.get("function"), dict)
                else {}
            )
            name = str(function_data.get("name", "")).strip()
            if not isinstance(tool_call_id, str) or not name:
                continue

            index[tool_call_id] = {
                "name": name,
                "arguments": _stringify_tool_arguments(
                    function_data.get("arguments")
                ),
            }

    return index


def _format_tool_result_message(
    tool_name: str,
    tool_arguments: str,
    result_content: str,
) -> str:
    """Serialize a tool result into a text block the upstream can consume."""
    return (
        "<tool_execution_result>\n"
        f"<tool_name>{tool_name}</tool_name>\n"
        f"<tool_arguments>{tool_arguments}</tool_arguments>\n"
        f"<tool_output>{result_content}</tool_output>\n"
        "</tool_execution_result>"
    )


def _format_assistant_tool_calls(tool_calls: List[Dict[str, Any]]) -> str:
    """Serialize historical assistant tool calls into a text block."""
    blocks: List[str] = []

    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue

        function_data = (
            tool_call.get("function")
            if isinstance(tool_call.get("function"), dict)
            else {}
        )
        name = str(function_data.get("name", "")).strip()
        if not name:
            continue

        arguments = _stringify_tool_arguments(function_data.get("arguments"))
        blocks.append(
            "<function_call>\n"
            f"<name>{name}</name>\n"
            f"<args_json>{arguments}</args_json>\n"
            "</function_call>"
        )

    if not blocks:
        return ""

    return "<function_calls>\n" + "\n".join(blocks) + "\n</function_calls>"


def _preprocess_openai_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Normalize OpenAI history into shapes accepted by the upstream service."""
    tool_call_index = _build_tool_call_index(messages)
    normalized: List[Dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue

        role = message.get("role")

        if role == "developer":
            converted = dict(message)
            converted["role"] = "system"
            normalized.append(converted)
            continue

        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            content = _extract_text_from_content(message.get("content"))
            tool_info = tool_call_index.get(
                tool_call_id,
                {
                    "name": str(message.get("name") or "unknown_tool"),
                    "arguments": "{}",
                },
            )
            normalized.append(
                {
                    "role": "user",
                    "content": _format_tool_result_message(
                        tool_info["name"],
                        tool_info["arguments"],
                        content,
                    ),
                }
            )
            continue

        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            content = _extract_text_from_content(message.get("content"))
            tool_calls_text = _format_assistant_tool_calls(message["tool_calls"])
            merged_content = "\n".join(
                part for part in (content, tool_calls_text) if part
            ).strip()
            normalized.append({"role": "assistant", "content": merged_content})
            continue

        normalized.append(dict(message))

    return normalized


def _extract_last_user_text(messages: List[Dict[str, Any]]) -> str:
    """Extract the last user text from the original OpenAI message history."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = _extract_text_from_content(message.get("content"))
        if content:
            return content
    return ""



class UpstreamClient:
    """当前服务使用的上游适配器。"""

    def __init__(self):
        self.name = "upstream"
        self.logger = logger
        self.api_endpoint = settings.API_ENDPOINT

        # 当前上游特定配置
        self.base_url = DEFAULT_ZAI_BASE_URL
        self.auth_url = f"{self.base_url}/api/v1/auths/"
        
        # 模型映射
        self.model_mapping = {
            settings.GLM45_MODEL: "0727-360B-API",  # GLM-4.5
            settings.GLM45_THINKING_MODEL: "0727-360B-API",  # GLM-4.5-Thinking
            settings.GLM45_SEARCH_MODEL: "0727-360B-API",  # GLM-4.5-Search
            settings.GLM45_AIR_MODEL: "0727-106B-API",  # GLM-4.5-Air
            settings.GLM46V_MODEL: "glm-4.6v",  # GLM-4.6V多模态
            settings.GLM5_MODEL: "glm-5",  # GLM-5
            settings.GLM47_MODEL: "glm-4.7",  # GLM-4.7
            settings.GLM47_THINKING_MODEL: "glm-4.7",  # GLM-4.7-Thinking
            settings.GLM47_SEARCH_MODEL: "glm-4.7",  # GLM-4.7-Search
            settings.GLM47_ADVANCED_SEARCH_MODEL: "glm-4.7",  # GLM-4.7-advanced-search
        }

    def _get_guest_retry_limit(self) -> int:
        """匿名号池可提供的最大重试预算。"""
        if not settings.ANONYMOUS_MODE:
            return 0

        guest_pool = get_guest_session_pool()
        if not guest_pool:
            return max(2, settings.GUEST_POOL_SIZE + 1)

        pool_status = guest_pool.get_pool_status()
        available_sessions = int(
            pool_status.get("valid_sessions")
            or pool_status.get("available_sessions")
            or 0
        )
        return max(2, available_sessions + 1)

    def _get_authenticated_retry_limit(self) -> int:
        """认证号池与静态 Token 可提供的最大重试预算。"""
        available_tokens = 0
        token_pool = get_token_pool()
        if token_pool:
            available_tokens = int(
                token_pool.get_pool_status().get("available_tokens", 0) or 0
            )

        return max(0, available_tokens)

    def _get_total_retry_limit(self) -> int:
        """综合认证号池与匿名号池的最大尝试次数。"""
        return max(
            1,
            self._get_authenticated_retry_limit() + self._get_guest_retry_limit(),
        )

    def _is_guest_auth(self, transformed: Dict[str, Any]) -> bool:
        """判断当前请求是否使用匿名会话。"""
        return str(transformed.get("auth_mode") or "") == "guest"

    def _should_retry_guest_session(
        self,
        status_code: int,
        is_concurrency_limited: bool,
        attempt: int,
        max_attempts: int,
        transformed: Dict[str, Any],
    ) -> bool:
        """判断匿名号池是否需要刷新会话后重试。"""
        return (
            self._is_guest_auth(transformed)
            and (status_code == 401 or is_concurrency_limited)
            and attempt + 1 < max_attempts
        )

    def _should_retry_authenticated_session(
        self,
        status_code: int,
        is_concurrency_limited: bool,
        attempt: int,
        max_attempts: int,
        transformed: Dict[str, Any],
    ) -> bool:
        """判断认证号池是否需要切号重试。"""
        current_token = str(transformed.get("token") or "")
        return (
            not self._is_guest_auth(transformed)
            and bool(current_token)
            and (status_code == 401 or is_concurrency_limited)
            and attempt + 1 < max_attempts
        )

    async def _release_guest_session(self, transformed: Dict[str, Any]):
        """释放当前匿名会话占用。"""
        if not self._is_guest_auth(transformed):
            return

        guest_pool = get_guest_session_pool()
        guest_user_id = str(
            transformed.get("guest_user_id") or transformed.get("user_id") or ""
        )
        if guest_pool and guest_user_id:
            guest_pool.release(guest_user_id)

    async def _report_guest_session_failure(
        self,
        transformed: Dict[str, Any],
        *,
        is_concurrency_limited: bool = False,
    ):
        """上报匿名会话失败并补齐新会话。"""
        if not self._is_guest_auth(transformed):
            return

        guest_pool = get_guest_session_pool()
        guest_user_id = str(
            transformed.get("guest_user_id") or transformed.get("user_id") or ""
        )
        if not guest_pool or not guest_user_id:
            return

        if is_concurrency_limited:
            await guest_pool.cleanup_idle_chats()

        await guest_pool.report_failure(guest_user_id)

    async def _refresh_guest_request(
        self,
        request: OpenAIRequest,
        attempt: int,
        excluded_tokens: Set[str],
        excluded_guest_user_ids: Set[str],
        failed_transformed: Dict[str, Any],
        is_concurrency_limited: bool = False,
    ) -> Dict[str, Any]:
        """匿名会话失效或并发受限后切换会话并重签请求。"""
        retry_number = attempt + 2
        self.logger.warning(
            "🔄 匿名会话不可用，正在切换匿名会话并进行第 "
            f"{retry_number} 次请求"
        )
        await self._report_guest_session_failure(
            failed_transformed,
            is_concurrency_limited=is_concurrency_limited,
        )
        return await self.transform_request(
            request,
            excluded_tokens=excluded_tokens,
            excluded_guest_user_ids=excluded_guest_user_ids,
        )

    async def _refresh_authenticated_request(
        self,
        request: OpenAIRequest,
        attempt: int,
        excluded_tokens: Set[str],
        excluded_guest_user_ids: Set[str],
    ) -> Dict[str, Any]:
        """认证模式下切换到下一枚 Token，并允许回退匿名池。"""
        retry_number = attempt + 2
        self.logger.warning(
            "🔄 检测到认证会话不可用，正在切换认证 Token/回退匿名池并进行第 "
            f"{retry_number} 次请求"
        )
        return await self.transform_request(
            request,
            excluded_tokens=excluded_tokens,
            excluded_guest_user_ids=excluded_guest_user_ids,
        )

    def _extract_upstream_error_details(
        self,
        status_code: int,
        error_text: str,
    ) -> Tuple[Optional[int], str]:
        """解析上游错误响应中的 code/message。"""
        parsed_code: Optional[int] = None
        parsed_message = (error_text or "").strip()

        try:
            payload = json.loads(error_text)
        except Exception:
            return parsed_code, parsed_message

        if not isinstance(payload, dict):
            return parsed_code, parsed_message

        candidates = [
            payload,
            payload.get("error") if isinstance(payload.get("error"), dict) else None,
            payload.get("detail") if isinstance(payload.get("detail"), dict) else None,
            payload.get("data") if isinstance(payload.get("data"), dict) else None,
        ]

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            code = candidate.get("code")
            if isinstance(code, int):
                parsed_code = code
            elif isinstance(code, str) and code.isdigit():
                parsed_code = int(code)

            for key in ("message", "msg", "detail", "error"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    parsed_message = value.strip()
                    break

            if parsed_code is not None or parsed_message:
                break

        return parsed_code, parsed_message

    def _is_concurrency_limited(
        self,
        status_code: int,
        error_code: Optional[int],
        error_message: str,
    ) -> bool:
        """判断是否为上游并发限制/429 场景。"""
        message = (error_message or "").casefold()
        return (
            status_code == 429
            or error_code == 429
            or "concurrency" in message
            or "too many requests" in message
            or "并发" in error_message
        )
    
    def get_supported_models(self) -> List[str]:
        """获取支持的模型列表"""
        return [
            settings.GLM45_MODEL,
            settings.GLM45_THINKING_MODEL,
            settings.GLM45_SEARCH_MODEL,
            settings.GLM45_AIR_MODEL,
            settings.GLM46V_MODEL,
            settings.GLM5_MODEL,
            settings.GLM47_MODEL,
            settings.GLM47_THINKING_MODEL,
            settings.GLM47_SEARCH_MODEL,
            settings.GLM47_ADVANCED_SEARCH_MODEL,
        ]

    def _requires_persisted_chat(self, upstream_model_id: str) -> bool:
        """需要挂载真实 chat 会话的上游模型。"""
        return bool(
            self._get_model_request_profile(upstream_model_id)["use_persisted_chat"]
        )

    def _get_model_request_profile(self, upstream_model_id: str) -> Dict[str, Any]:
        """返回模型专属的请求配置。"""
        if upstream_model_id == "glm-4.6v":
            return {
                "use_persisted_chat": True,
                "preview_mode": False,
                "mcp_servers": list(GLM46V_MCP_SERVERS),
                "feature_entries": [dict(item) for item in GLM46V_SELECTED_FEATURES],
                "default_enable_thinking": True,
            }

        if upstream_model_id == "glm-5":
            return {
                "use_persisted_chat": False,
                "preview_mode": True,
                "mcp_servers": [],
                "feature_entries": [],
                "default_enable_thinking": True,
            }

        return {
            "use_persisted_chat": upstream_model_id == "glm-4.7",
            "preview_mode": True,
            "mcp_servers": [],
            "feature_entries": [],
            "default_enable_thinking": None,
        }

    def _build_request_variables(self) -> Dict[str, str]:
        """构建上游请求需要的运行时变量。"""
        now = datetime.now()
        return {
            "{{USER_NAME}}": "Guest",
            "{{USER_LOCATION}}": "Unknown",
            "{{CURRENT_DATETIME}}": now.strftime("%Y-%m-%d %H:%M:%S"),
            "{{CURRENT_DATE}}": now.strftime("%Y-%m-%d"),
            "{{CURRENT_TIME}}": now.strftime("%H:%M:%S"),
            "{{CURRENT_WEEKDAY}}": now.strftime("%A"),
            "{{CURRENT_TIMEZONE}}": DEFAULT_TIMEZONE,
            "{{USER_LANGUAGE}}": DEFAULT_LANGUAGE,
        }

    def _build_browser_query_params(
        self,
        *,
        chat_id: str,
        token: str,
        user_id: str,
        user_agent: str,
        timestamp_ms: int,
    ) -> Dict[str, str]:
        """构建 GLM-4.7 所需的浏览器指纹查询参数。"""
        now = datetime.now(timezone.utc)
        browser_name = "Chrome"
        if "Edg/" in user_agent:
            browser_name = "Microsoft Edge"
        elif "Firefox/" in user_agent:
            browser_name = "Firefox"
        elif "Safari/" in user_agent and "Chrome/" not in user_agent:
            browser_name = "Safari"

        return {
            "version": DEFAULT_CLIENT_VERSION,
            "platform": DEFAULT_PLATFORM,
            "token": token,
            "user_agent": user_agent,
            "language": DEFAULT_LANGUAGE,
            "languages": DEFAULT_LANGUAGE,
            "timezone": DEFAULT_TIMEZONE,
            "cookie_enabled": "true",
            "screen_width": DEFAULT_SCREEN_WIDTH,
            "screen_height": DEFAULT_SCREEN_HEIGHT,
            "screen_resolution": DEFAULT_SCREEN_RESOLUTION,
            "viewport_height": DEFAULT_VIEWPORT_HEIGHT,
            "viewport_width": DEFAULT_VIEWPORT_WIDTH,
            "viewport_size": DEFAULT_VIEWPORT_SIZE,
            "color_depth": DEFAULT_COLOR_DEPTH,
            "pixel_ratio": DEFAULT_PIXEL_RATIO,
            "current_url": f"{self.base_url}/c/{chat_id}",
            "pathname": f"/c/{chat_id}",
            "search": "",
            "hash": "",
            "host": "chat.z.ai",
            "hostname": "chat.z.ai",
            "protocol": "https:",
            "referrer": "",
            "title": DEFAULT_PAGE_TITLE,
            "timezone_offset": DEFAULT_TIMEZONE_OFFSET,
            "local_time": (
                now.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{now.microsecond // 1000:03d}Z"
            ),
            "utc_time": now.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "is_mobile": "false",
            "is_touch": "false",
            "max_touch_points": DEFAULT_MAX_TOUCH_POINTS,
            "browser_name": browser_name,
            "os_name": "Windows",
            "signature_timestamp": str(timestamp_ms),
        }

    def _build_signed_completion_request(
        self,
        *,
        prompt: str,
        chat_id: str,
        token: str,
        user_id: str,
        user_agent: str,
        use_browser_fingerprint: bool,
    ) -> Tuple[str, str, str]:
        """构建上游 completions 的签名 URL 与请求头元数据。"""
        timestamp_ms = int(time.time() * 1000)
        request_id = generate_uuid()
        core_params = {
            "requestId": request_id,
            "timestamp": str(timestamp_ms),
            "user_id": user_id,
        }
        canonical_payload = ",".join(
            f"{key},{value}" for key, value in sorted(core_params.items())
        )
        signature = generate_signature(
            e=canonical_payload,
            t=prompt or "",
            s=timestamp_ms,
        )["signature"]
        query_params = dict(core_params)
        if use_browser_fingerprint:
            query_params.update(
                self._build_browser_query_params(
                    chat_id=chat_id,
                    token=token,
                    user_id=user_id,
                    user_agent=user_agent,
                    timestamp_ms=timestamp_ms,
                )
            )
        else:
            query_params.update(
                {
                    "token": token,
                    "version": DEFAULT_CLIENT_VERSION,
                    "platform": DEFAULT_PLATFORM,
                    "current_url": f"{self.base_url}/c/{chat_id}",
                    "pathname": f"/c/{chat_id}",
                    "signature_timestamp": str(timestamp_ms),
                }
            )

        return (
            f"{self.api_endpoint}?{urlencode(query_params)}",
            signature,
            str(timestamp_ms),
        )

    async def _create_upstream_chat(
        self,
        *,
        prompt: str,
        model: str,
        token: str,
        headers: Dict[str, str],
        enable_thinking: bool,
        web_search: bool,
        user_message_id: Optional[str] = None,
        files: Optional[List[Dict[str, Any]]] = None,
        feature_entries: Optional[List[Dict[str, Any]]] = None,
        mcp_servers: Optional[List[str]] = None,
    ) -> str:
        """为 GLM-4.7 系列创建上游真实 chat 会话。"""
        init_content = prompt[:CHAT_BOOTSTRAP_MAX_CONTENT_LEN]
        if len(prompt) > CHAT_BOOTSTRAP_MAX_CONTENT_LEN:
            init_content = init_content + "..."

        message_id = user_message_id or generate_uuid()
        timestamp_seconds = int(time.time())
        chat_features = (
            [dict(item) for item in feature_entries]
            if feature_entries
            else [
                {
                    "type": "tool_selector",
                    "server": "tool_selector_h",
                    "status": "hidden",
                }
            ]
        )
        body = {
            "chat": {
                "id": "",
                "title": "新聊天",
                "models": [model],
                "params": {},
                "history": {
                    "messages": {
                        message_id: {
                            "id": message_id,
                            "parentId": None,
                            "childrenIds": [],
                            "role": "user",
                            "content": init_content,
                            **({"files": [dict(item) for item in files]} if files else {}),
                            "timestamp": timestamp_seconds,
                            "models": [model],
                        }
                    },
                    "currentId": message_id,
                },
                "tags": [],
                "flags": [],
                "features": chat_features,
                "mcp_servers": list(mcp_servers or []),
                "enable_thinking": enable_thinking,
                "auto_web_search": web_search,
                "message_version": 1,
                "extra": {},
                "timestamp": int(time.time() * 1000),
            }
        }
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": headers["User-Agent"],
            "Accept-Language": headers.get("Accept-Language", DEFAULT_LANGUAGE),
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self._build_timeout(),
            limits=self._build_limits(),
            proxy=self._get_proxy_config(),
            follow_redirects=True,
        ) as client:
            response = await client.post(
                "/api/v1/chats/new",
                headers=request_headers,
                json=body,
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"上游创建 chat 失败: {response.status_code} {response.text}"
            )

        payload = response.json()
        chat_id = str(payload.get("id") or payload.get("chat", {}).get("id") or "")
        if not chat_id:
            raise RuntimeError("上游创建 chat 成功但未返回 chat_id")
        return chat_id

    def _build_glm47_completion_body(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        prompt: str,
        chat_id: str,
        enable_thinking: bool,
        web_search: bool,
        files: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Any,
        temperature: Optional[float],
        max_tokens: Optional[int],
        mcp_servers: List[str],
        preview_mode: bool,
        feature_entries: Optional[List[Dict[str, Any]]],
        message_id: str,
        current_user_message_id: str,
        current_user_message_parent_id: Optional[str],
    ) -> Dict[str, Any]:
        """构建兼容持久化 chat 模型的精简 completions 请求体。"""
        params: Dict[str, Any] = {}
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens

        body: Dict[str, Any] = {
            "stream": True,
            "model": model,
            "messages": messages,
            "signature_prompt": prompt,
            "params": params,
            "extra": {},
            "features": {
                "image_generation": False,
                "web_search": web_search,
                "auto_web_search": web_search,
                "preview_mode": preview_mode,
                "flags": [],
                "enable_thinking": enable_thinking,
            },
            "variables": self._build_request_variables(),
            "chat_id": chat_id,
            "id": message_id,
            "current_user_message_id": current_user_message_id,
            "current_user_message_parent_id": current_user_message_parent_id,
            "background_tasks": {
                "title_generation": True,
                "tags_generation": True,
            },
        }
        if files:
            body["files"] = files
        if mcp_servers:
            body["mcp_servers"] = mcp_servers
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        return body

    def _clean_reasoning_delta(self, delta_content: str) -> str:
        """清理思考阶段的 details 包裹内容。"""
        if not delta_content:
            return ""

        if delta_content.startswith("<details"):
            if "</summary>\n>" in delta_content:
                return delta_content.split("</summary>\n>")[-1].strip()
            if "</summary>\n" in delta_content:
                return delta_content.split("</summary>\n")[-1].lstrip("> ").strip()

        return delta_content

    def _extract_answer_content(self, text: str) -> str:
        """提取思考结束后的答案正文。"""
        if not text:
            return ""

        if "</details>\n" in text:
            return text.split("</details>\n")[-1]

        if "</details>" in text:
            return text.split("</details>")[-1].lstrip()

        return text

    def _normalize_tool_calls(
        self,
        raw_tool_calls: Any,
        start_index: int = 0,
    ) -> List[Dict[str, Any]]:
        """标准化上游工具调用为 OpenAI 兼容格式。"""
        if not raw_tool_calls:
            return []

        tool_calls = raw_tool_calls if isinstance(raw_tool_calls, list) else [raw_tool_calls]
        normalized: List[Dict[str, Any]] = []

        for offset, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue

            function_data = tool_call.get("function") or {}
            normalized.append(
                {
                    "index": tool_call.get("index", start_index + offset),
                    "id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": function_data.get("name", ""),
                        "arguments": function_data.get("arguments", ""),
                    },
                }
            )

        return normalized

    def _format_search_results(self, data: Dict[str, Any]) -> str:
        """将上游搜索结果格式化为可追加的 Markdown 引用。"""
        search_info = data.get("results") or data.get("sources") or data.get("citations")
        if not isinstance(search_info, list) or not search_info:
            return ""

        citations = []
        for index, item in enumerate(search_info, 1):
            if not isinstance(item, dict):
                continue

            title = item.get("title") or item.get("name") or f"Result {index}"
            url = item.get("url") or item.get("link")
            if url:
                citations.append(f"[{index}] [{title}]({url})")

        if not citations:
            return ""

        return "\n\n---\n" + "\n".join(citations)

    def _get_proxy_config(self) -> Optional[str]:
        """Get proxy configuration from settings"""
        # In httpx 0.28.1, proxy parameter expects a single URL string
        # Support HTTP_PROXY, HTTPS_PROXY and SOCKS5_PROXY
        
        if settings.HTTPS_PROXY:
            self.logger.info(f"🔄 使用HTTPS代理: {settings.HTTPS_PROXY}")
            return settings.HTTPS_PROXY
            
        if settings.HTTP_PROXY:
            self.logger.info(f"🔄 使用HTTP代理: {settings.HTTP_PROXY}")
            return settings.HTTP_PROXY
            
        if settings.SOCKS5_PROXY:
            self.logger.info(f"🔄 使用SOCKS5代理: {settings.SOCKS5_PROXY}")
            return settings.SOCKS5_PROXY

        return None

    def _build_timeout(self, read_timeout: float = 30.0) -> httpx.Timeout:
        """Create httpx timeout settings tuned for upstream chat traffic."""
        return httpx.Timeout(
            connect=5.0,
            read=read_timeout,
            write=10.0,
            pool=5.0,
        )

    def _build_limits(self) -> httpx.Limits:
        """Create conservative connection-pool limits for upstream requests."""
        return httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
        )

    async def _fetch_direct_guest_auth(self) -> Dict[str, Any]:
        """匿名号池缺席时，兜底直连拉取一个访客令牌。"""
        max_retries = 3

        for retry_count in range(max_retries):
            try:
                headers = get_dynamic_headers()
                self.logger.debug(
                    f"尝试获取访客令牌 (第{retry_count + 1}次): {self.auth_url}"
                )

                proxies = self._get_proxy_config()
                async with httpx.AsyncClient(
                    timeout=self._build_timeout(),
                    follow_redirects=True,
                    limits=self._build_limits(),
                    proxy=proxies,
                ) as client:
                    response = await client.get(self.auth_url, headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    token = str(data.get("token") or "").strip()
                    if token:
                        user_id = str(
                            data.get("id")
                            or data.get("user_id")
                            or _extract_user_id_from_token(token)
                        )
                        username = str(
                            data.get("name")
                            or str(data.get("email") or "").split("@")[0]
                            or "Guest"
                        )
                        self.logger.info(
                            f"✅ 直连获取匿名令牌成功: {token[:20]}..."
                        )
                        return {
                            "token": token,
                            "user_id": user_id,
                            "username": username or "Guest",
                            "auth_mode": "guest",
                            "token_source": "guest_direct",
                            "guest_user_id": user_id,
                        }

                    self.logger.warning(f"响应中未找到 token 字段: {data}")
                elif response.status_code == 405:
                    self.logger.error(
                        "🚫 请求被 WAF 拦截 (405)，无法直连获取匿名令牌"
                    )
                    break
                else:
                    self.logger.warning(
                        f"直连获取匿名令牌失败，状态码: {response.status_code}"
                    )
            except httpx.TimeoutException as exc:
                self.logger.warning(
                    f"直连获取匿名令牌超时 (第{retry_count + 1}次): {exc}"
                )
            except httpx.ConnectError as exc:
                self.logger.warning(
                    f"直连获取匿名令牌连接错误 (第{retry_count + 1}次): {exc}"
                )
            except json.JSONDecodeError as exc:
                self.logger.warning(
                    f"直连获取匿名令牌 JSON 解析错误 (第{retry_count + 1}次): {exc}"
                )
            except Exception as exc:
                self.logger.warning(
                    f"直连获取匿名令牌失败 (第{retry_count + 1}次): {exc}"
                )

            if retry_count + 1 < max_retries:
                await asyncio.sleep(2)

        return {
            "token": "",
            "user_id": "guest",
            "username": "Guest",
            "auth_mode": "guest",
            "token_source": "guest_direct",
            "guest_user_id": None,
        }

    async def get_auth_info(
        self,
        excluded_tokens: Optional[Set[str]] = None,
        excluded_guest_user_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """优先获取认证 Token，必要时回退匿名会话池。"""
        token_pool = get_token_pool()
        if token_pool:
            token = token_pool.get_next_token(exclude_tokens=excluded_tokens)
            if token:
                user_id = _extract_user_id_from_token(token)
                self.logger.debug(f"从认证号池获取令牌: {token[:20]}...")
                return {
                    "token": token,
                    "user_id": user_id,
                    "username": "User",
                    "auth_mode": "authenticated",
                    "token_source": "auth_pool",
                    "guest_user_id": None,
                }

        if settings.ANONYMOUS_MODE:
            guest_pool = get_guest_session_pool()
            if guest_pool:
                try:
                    session = await guest_pool.acquire(
                        exclude_user_ids=excluded_guest_user_ids
                    )
                    self.logger.info(
                        "🫥 认证池不可用，回退匿名会话池: "
                        f"user_id={session.user_id}"
                    )
                    return {
                        "token": session.token,
                        "user_id": session.user_id,
                        "username": session.username,
                        "auth_mode": "guest",
                        "token_source": "guest_pool",
                        "guest_user_id": session.user_id,
                    }
                except Exception as exc:
                    self.logger.warning(f"匿名会话池获取失败，转为直连访客鉴权: {exc}")

            return await self._fetch_direct_guest_auth()

        self.logger.error("❌ 无法获取有效的上游令牌")
        return {
            "token": "",
            "user_id": "",
            "username": "",
            "auth_mode": "authenticated",
            "token_source": "none",
            "guest_user_id": None,
        }

    async def mark_token_failure(self, token: str, error: Exception = None):
        """标记token使用失败"""
        token_pool = get_token_pool()
        if token_pool:
            await token_pool.record_token_failure(token, error)

    async def upload_image(
        self,
        data_url: str,
        chat_id: str,
        token: str,
        user_id: str,
        auth_mode: str = "authenticated",
    ) -> Optional[Dict]:
        """上传 base64 编码的图片到上游服务器。

        Args:
            data_url: data:image/xxx;base64,... 格式的图片数据
            chat_id: 当前对话ID
            token: 认证令牌
            user_id: 用户ID
            auth_mode: 当前鉴权模式，guest 模式下禁止上传

        Returns:
            上传成功返回完整的文件信息字典，失败返回 None
        """
        if auth_mode == "guest" or not data_url.startswith("data:"):
            return None

        try:
            # 解析 data URL
            header, encoded = data_url.split(",", 1)
            mime_type = header.split(";")[0].split(":")[1] if ":" in header else "image/jpeg"

            # 解码 base64 数据
            image_data = base64.b64decode(encoded)
            filename = str(uuid.uuid4())

            self.logger.debug(f"📤 上传图片: {filename}, 大小: {len(image_data)} bytes")

            # 构建上传请求
            upload_url = f"{self.base_url}/api/v1/files/"
            headers = {
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Origin": f"{self.base_url}",
                "Pragma": "no-cache",
                "Referer": (
                    f"{self.base_url}/c/{chat_id}" if chat_id else f"{self.base_url}/"
                ),
                "Sec-Ch-Ua": '"Microsoft Edge";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
                "Authorization": f"Bearer {token}",
            }

            # Get proxy configuration
            proxies = self._get_proxy_config()

            # 使用 httpx 上传文件
            async with httpx.AsyncClient(
                timeout=self._build_timeout(),
                limits=self._build_limits(),
                proxy=proxies,
            ) as client:
                files = {
                    "file": (filename, image_data, mime_type)
                }
                response = await client.post(upload_url, files=files, headers=headers)

                if response.status_code == 200:
                    result = response.json()
                    file_id = result.get("id")
                    file_name = result.get("filename")
                    file_size = len(image_data)

                    self.logger.info(f"✅ 图片上传成功: {file_id}_{file_name}")

                    # 返回符合上游格式的文件信息
                    current_timestamp = int(time.time())
                    return {
                        "type": "image",
                        "file": {
                            "id": file_id,
                            "user_id": user_id,
                            "hash": None,
                            "filename": file_name,
                            "data": {},
                            "meta": {
                                "name": file_name,
                                "content_type": mime_type,
                                "size": file_size,
                                "data": {},
                            },
                            "created_at": current_timestamp,
                            "updated_at": current_timestamp
                        },
                        "id": file_id,
                        "url": f"/api/v1/files/{file_id}/content",
                        "name": file_name,
                        "status": "uploaded",
                        "size": file_size,
                        "error": "",
                        "itemId": str(uuid.uuid4()),
                        "media": "image"
                    }
                else:
                    self.logger.error(f"❌ 图片上传失败: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            self.logger.error(f"❌ 图片上传异常: {e}")
            return None

    async def transform_request(
        self,
        request: OpenAIRequest,
        excluded_tokens: Optional[Set[str]] = None,
        excluded_guest_user_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """转换 OpenAI 请求为上游格式。"""
        self.logger.info(f"🔄 转换 OpenAI 请求到上游格式: {request.model}")

        raw_messages = [
            message.model_dump(exclude_none=True)
            for message in request.messages
        ]
        normalized_messages = _preprocess_openai_messages(raw_messages)

        auth_info = await self.get_auth_info(
            excluded_tokens=excluded_tokens,
            excluded_guest_user_ids=excluded_guest_user_ids,
        )
        token = str(auth_info.get("token") or "")
        if not token:
            raise RuntimeError("无法获取上游认证令牌")

        user_id = str(auth_info.get("user_id") or _extract_user_id_from_token(token))
        auth_mode = str(auth_info.get("auth_mode") or "authenticated")
        token_source = str(auth_info.get("token_source") or "unknown")
        guest_user_id = auth_info.get("guest_user_id")
        # 确定请求的模型特性
        last_user_text = _extract_last_user_text(raw_messages)
        requested_model = request.model
        is_thinking_model = "-thinking" in requested_model.casefold()
        is_search_model = "-search" in requested_model.casefold()
        is_advanced_search = requested_model == settings.GLM47_ADVANCED_SEARCH_MODEL
        upstream_model_id = self.model_mapping.get(requested_model, "0727-360B-API")
        tools = request.tools if settings.TOOL_SUPPORT and request.tools else None
        tool_choice = getattr(request, "tool_choice", None)
        model_profile = self._get_model_request_profile(upstream_model_id)
        enable_thinking = request.enable_thinking
        if enable_thinking is None:
            default_enable_thinking = model_profile["default_enable_thinking"]
            enable_thinking = (
                default_enable_thinking
                if default_enable_thinking is not None
                else is_thinking_model
            )

        web_search = request.web_search
        if web_search is None:
            web_search = is_search_model or is_advanced_search

        use_persisted_chat = bool(model_profile["use_persisted_chat"])
        preview_mode = bool(model_profile["preview_mode"])
        feature_entries = list(model_profile["feature_entries"])
        persisted_user_message_id = generate_uuid() if use_persisted_chat else None
        persisted_assistant_message_id = generate_uuid() if use_persisted_chat else None

        mcp_servers = list(model_profile["mcp_servers"])
        if is_advanced_search and "advanced-search" not in mcp_servers:
            mcp_servers.append("advanced-search")
            self.logger.info("🔍 检测到高级搜索模型，添加 advanced-search MCP 服务器")

        headers = get_dynamic_headers(
            browser_type="chrome" if use_persisted_chat else None,
        )
        chat_id = generate_uuid()

        # 处理消息格式 - 上游使用单独的 files 字段传递图片
        messages = []
        files = []
        upload_chat_id = "" if use_persisted_chat else chat_id

        for msg in normalized_messages:
            role = str(msg.get("role", "user"))
            content = msg.get("content")

            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue

            if not isinstance(content, list):
                continue

            text_parts = []
            image_parts = []
            for part in content:
                image_url = None
                if hasattr(part, "type"):
                    if part.type == "text" and hasattr(part, "text"):
                        text_parts.append(part.text or "")
                    elif part.type == "image_url" and hasattr(part, "image_url"):
                        if hasattr(part.image_url, "url"):
                            image_url = part.image_url.url
                        elif (
                            isinstance(part.image_url, dict)
                            and "url" in part.image_url
                        ):
                            image_url = part.image_url["url"]
                elif isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        image_url = part.get("image_url", {}).get("url", "")
                elif isinstance(part, str):
                    text_parts.append(part)

                if not image_url:
                    continue

                self.logger.debug(f"✅ 检测到图片: {image_url[:50]}...")
                if image_url.startswith("data:") and auth_mode != "guest":
                    self.logger.info("🔄 上传 base64 图片到上游服务")
                    file_info = await self.upload_image(
                        image_url,
                        upload_chat_id,
                        token,
                        user_id,
                        auth_mode=auth_mode,
                    )
                    if not file_info:
                        self.logger.warning("⚠️ 图片上传失败")
                        text_parts.append("[系统提示: 图片上传失败]")
                        continue

                    files.append(file_info)
                    self.logger.info("✅ 图片已添加到 files 数组")
                    if persisted_user_message_id:
                        file_info["ref_user_msg_id"] = persisted_user_message_id
                    image_ref = str(file_info["id"])
                    image_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": image_ref},
                        }
                    )
                    self.logger.debug(f"📎 图片引用: {image_ref}")
                    continue

                if auth_mode != "guest":
                    self.logger.warning("⚠️ 非 base64 图片或匿名模式，保留原始URL")
                image_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    }
                )

            message_content = []
            combined_text = " ".join(text_parts).strip()
            if combined_text:
                message_content.append({"type": "text", "text": combined_text})
            message_content.extend(image_parts)
            if message_content:
                messages.append({"role": role, "content": message_content})

        if use_persisted_chat:
            chat_id = await self._create_upstream_chat(
                prompt=last_user_text,
                model=upstream_model_id,
                token=token,
                headers=headers,
                enable_thinking=enable_thinking,
                web_search=web_search,
                user_message_id=persisted_user_message_id,
                files=files or None,
                feature_entries=feature_entries or None,
                mcp_servers=mcp_servers or None,
            )
            self.logger.info(f"🧩 已为 {requested_model} 创建上游 chat: {chat_id}")
        headers["Referer"] = f"{self.base_url}/c/{chat_id}"

        if use_persisted_chat:
            body = self._build_glm47_completion_body(
                model=upstream_model_id,
                messages=messages,
                prompt=last_user_text,
                chat_id=chat_id,
                enable_thinking=enable_thinking,
                web_search=web_search,
                files=files,
                tools=tools,
                tool_choice=tool_choice,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                mcp_servers=mcp_servers,
                preview_mode=preview_mode,
                feature_entries=feature_entries or None,
                message_id=persisted_assistant_message_id or generate_uuid(),
                current_user_message_id=persisted_user_message_id or generate_uuid(),
                current_user_message_parent_id=None,
            )
        else:
            message_id = generate_uuid()
            session_id = generate_uuid()
            body = {
                "stream": True,
                "model": upstream_model_id,
                "messages": messages,
                "signature_prompt": last_user_text,
                "files": files,
                "params": {},
                "extra": {},
                "features": {
                    "image_generation": False,
                    "web_search": web_search,
                    "auto_web_search": web_search,
                    "preview_mode": preview_mode,
                    "flags": [],
                    "features": [
                        dict(item)
                        for item in (feature_entries or DEFAULT_COMPLETION_FEATURES)
                    ],
                    "enable_thinking": enable_thinking,
                },
                "background_tasks": {
                    "title_generation": False,
                    "tags_generation": False,
                },
                "mcp_servers": mcp_servers,
                "variables": self._build_request_variables(),
                "model_item": {
                    "id": upstream_model_id,
                    "name": requested_model,
                    "owned_by": settings.SERVICE_NAME,
                },
                "chat_id": chat_id,
                "id": message_id,
                "session_id": session_id,
                "current_user_message_id": message_id,
                "current_user_message_parent_id": None,
            }
            if tools:
                body["tools"] = tools
                if tool_choice is not None:
                    body["tool_choice"] = tool_choice
                self.logger.info(f"🔧 工具调用将直接透传到上游: {len(tools)} 个工具")
            else:
                body["tools"] = None
            if request.temperature is not None:
                body["params"]["temperature"] = request.temperature
            if request.max_tokens is not None:
                body["params"]["max_tokens"] = request.max_tokens

        try:
            signed_url, signature, timestamp_ms = self._build_signed_completion_request(
                prompt=last_user_text,
                chat_id=chat_id,
                token=token,
                user_id=user_id,
                user_agent=headers["User-Agent"],
                use_browser_fingerprint=use_persisted_chat,
            )
            logger.debug(
                "[上游] 生成签名成功: %s... (user_id=%s, timestamp=%s)",
                signature[:16],
                user_id,
                timestamp_ms,
            )
        except Exception as e:
            logger.error(f"[上游] 签名生成失败: {e}")
            signature = ""
            timestamp_ms = "0"
            signed_url = self.api_endpoint

        fe_version = headers.get("X-FE-Version") or get_latest_fe_version()
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "*/*" if use_persisted_chat else "application/json",
                "X-FE-Version": fe_version,
                "X-Signature": signature,
            }
        )

        logger.debug(
            "[上游] 请求头: Authorization=Bearer *****, X-Signature=%s...",
            signature[:16] if signature else "(空)",
        )
        logger.debug(
            "[上游] URL 参数: timestamp=%s, user_id=%s, persisted_chat=%s",
            timestamp_ms,
            user_id,
            use_persisted_chat,
        )
        
        # 存储当前token用于错误处理
        self._current_token = token

        return {
            "url": signed_url,
            "headers": headers,
            "body": body,
            "token": token,
            "chat_id": chat_id,
            "model": requested_model,
            "user_id": user_id,
            "auth_mode": auth_mode,
            "token_source": token_source,
            "guest_user_id": guest_user_id,
        }
    
    async def chat_completion(
        self,
        request: OpenAIRequest,
        **kwargs
    ) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """聊天完成接口。"""
        self.logger.info(f"🔄 {self.name} 处理请求: {request.model}")
        self.logger.debug(f"  消息数量: {len(request.messages)}")
        self.logger.debug(f"  流式模式: {request.stream}")

        try:
            transformed = await self.transform_request(request)

            if request.stream:
                return self._create_stream_response(request, transformed)

            proxies = self._get_proxy_config()
            max_attempts = self._get_total_retry_limit()
            excluded_tokens: Set[str] = set()
            excluded_guest_user_ids: Set[str] = set()

            for attempt in range(max_attempts):
                async with httpx.AsyncClient(
                    timeout=self._build_timeout(read_timeout=60.0),
                    limits=self._build_limits(),
                    proxy=proxies,
                ) as client:
                    response = await client.post(
                        transformed["url"],
                        headers=transformed["headers"],
                        json=transformed["body"],
                    )

                error_code, error_message = self._extract_upstream_error_details(
                    response.status_code,
                    response.text,
                )
                is_concurrency_limited = self._is_concurrency_limited(
                    response.status_code,
                    error_code,
                    error_message,
                )

                if self._should_retry_guest_session(
                    response.status_code,
                    is_concurrency_limited,
                    attempt,
                    max_attempts,
                    transformed,
                ):
                    guest_user_id = str(
                        transformed.get("guest_user_id")
                        or transformed.get("user_id")
                        or ""
                    )
                    if guest_user_id:
                        excluded_guest_user_ids.add(guest_user_id)
                    transformed = await self._refresh_guest_request(
                        request,
                        attempt,
                        excluded_tokens,
                        excluded_guest_user_ids,
                        transformed,
                        is_concurrency_limited=is_concurrency_limited,
                    )
                    continue

                if self._should_retry_authenticated_session(
                    response.status_code,
                    is_concurrency_limited,
                    attempt,
                    max_attempts,
                    transformed,
                ):
                    current_token = str(transformed.get("token") or "")
                    if current_token:
                        excluded_tokens.add(current_token)
                        await self.mark_token_failure(
                            current_token,
                            Exception(error_message or "上游认证会话不可用"),
                        )
                        self.logger.warning(
                            "⚠️ 认证会话不可用，准备切换认证 Token/回退匿名池: "
                            f"{current_token[:20]}..."
                        )
                    transformed = await self._refresh_authenticated_request(
                        request,
                        attempt,
                        excluded_tokens,
                        excluded_guest_user_ids,
                    )
                    continue

                if not response.is_success:
                    error_msg = f"上游 API 错误: {response.status_code}"
                    if not self._is_guest_auth(transformed):
                        current_token = str(transformed.get("token") or "")
                        if current_token:
                            await self.mark_token_failure(
                                current_token,
                                Exception(error_message or error_msg),
                            )
                    await self._release_guest_session(transformed)
                    self.logger.error(f"❌ {self.name} 响应失败: {error_msg}")
                    return handle_error(Exception(error_message or error_msg))

                try:
                    result = await self.transform_response(response, request, transformed)
                finally:
                    await self._release_guest_session(transformed)

                if not self._is_guest_auth(transformed):
                    current_token = str(transformed.get("token") or "")
                    if current_token:
                        token_pool = get_token_pool()
                        if token_pool:
                            await token_pool.record_token_success(current_token)

                return result

        except Exception as e:
            self.logger.error(f"❌ {self.name} 响应失败: {str(e)}")
            return handle_error(e, "请求处理")

    async def _create_stream_response(
        self,
        request: OpenAIRequest,
        transformed: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """创建流式响应，并在首包前支持双池重试。"""
        max_attempts = self._get_total_retry_limit()
        excluded_tokens: Set[str] = set()
        excluded_guest_user_ids: Set[str] = set()
        current_token = str(transformed.get("token") or "")

        try:
            proxies = self._get_proxy_config()

            async with httpx.AsyncClient(
                timeout=self._build_timeout(read_timeout=180.0),
                http2=True,
                limits=self._build_limits(),
                proxy=proxies,
            ) as client:
                for attempt in range(max_attempts):
                    self.logger.info(f"🎯 发送请求到上游: {transformed['url']}")
                    async with client.stream(
                        "POST",
                        transformed["url"],
                        json=transformed["body"],
                        headers=transformed["headers"],
                    ) as response:
                        error_text = await response.aread() if response.status_code != 200 else b""
                        error_msg = error_text.decode("utf-8", errors="ignore")
                        error_code, parsed_error_message = (
                            self._extract_upstream_error_details(
                                response.status_code,
                                error_msg,
                            )
                            if response.status_code != 200
                            else (None, "")
                        )
                        is_concurrency_limited = self._is_concurrency_limited(
                            response.status_code,
                            error_code,
                            parsed_error_message,
                        )

                        if self._should_retry_guest_session(
                            response.status_code,
                            is_concurrency_limited,
                            attempt,
                            max_attempts,
                            transformed,
                        ):
                            guest_user_id = str(
                                transformed.get("guest_user_id")
                                or transformed.get("user_id")
                                or ""
                            )
                            if guest_user_id:
                                excluded_guest_user_ids.add(guest_user_id)
                            transformed = await self._refresh_guest_request(
                                request,
                                attempt,
                                excluded_tokens,
                                excluded_guest_user_ids,
                                transformed,
                                is_concurrency_limited=is_concurrency_limited,
                            )
                            current_token = str(transformed.get("token") or "")
                            continue

                        if self._should_retry_authenticated_session(
                            response.status_code,
                            is_concurrency_limited,
                            attempt,
                            max_attempts,
                            transformed,
                        ):
                            if current_token:
                                excluded_tokens.add(current_token)
                                await self.mark_token_failure(
                                    current_token,
                                    Exception(
                                        parsed_error_message or "上游认证会话不可用"
                                    ),
                                )
                                self.logger.warning(
                                    "⚠️ 流式请求命中认证会话限制，准备切号/回退匿名池: "
                                    f"{current_token[:20]}..."
                                )
                            transformed = await self._refresh_authenticated_request(
                                request,
                                attempt,
                                excluded_tokens,
                                excluded_guest_user_ids,
                            )
                            current_token = str(transformed.get("token") or "")
                            continue

                        if response.status_code != 200:
                            self.logger.error(f"❌ 上游返回错误: {response.status_code}")
                            if error_msg:
                                self.logger.error(f"❌ 错误详情: {error_msg}")

                            if not self._is_guest_auth(transformed) and current_token:
                                await self.mark_token_failure(
                                    current_token,
                                    Exception(
                                        parsed_error_message
                                        or f"Upstream error: {response.status_code}"
                                    ),
                                )
                            await self._release_guest_session(transformed)

                            if response.status_code == 405:
                                self.logger.error(
                                    "🚫 请求被上游 WAF 拦截，可能是请求头或签名异常"
                                )
                                error_response = {
                                    "error": {
                                        "message": (
                                            "请求被上游WAF拦截(405 Method Not Allowed),"
                                            "可能是请求头或签名异常,请稍后重试..."
                                        ),
                                        "type": "waf_blocked",
                                        "code": 405,
                                    }
                                }
                            else:
                                error_response = {
                                    "error": {
                                        "message": parsed_error_message
                                        or f"Upstream error: {response.status_code}",
                                        "type": "upstream_error",
                                        "code": error_code or response.status_code,
                                    }
                                }
                            yield f"data: {json.dumps(error_response)}\n\n"
                            yield "data: [DONE]\n\n"
                            return

                        chat_id = transformed["chat_id"]
                        model = transformed["model"]
                        try:
                            async for chunk in self._handle_stream_response(
                                response,
                                chat_id,
                                model,
                                request,
                                transformed,
                            ):
                                yield chunk
                        finally:
                            await self._release_guest_session(transformed)

                        if not self._is_guest_auth(transformed) and current_token:
                            token_pool = get_token_pool()
                            if token_pool:
                                await token_pool.record_token_success(current_token)
                        return
        except Exception as e:
            self.logger.error(f"❌ 流处理错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            if self._is_guest_auth(transformed):
                await self._release_guest_session(transformed)
            elif current_token:
                await self.mark_token_failure(current_token, e)

            error_response = {
                "error": {
                    "message": str(e),
                    "type": "stream_error"
                }
            }
            yield f"data: {json.dumps(error_response)}\n\n"
            yield "data: [DONE]\n\n"
            return

    async def transform_response(
        self, 
        response: httpx.Response, 
        request: OpenAIRequest,
        transformed: Dict[str, Any]
    ) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """转换上游响应为 OpenAI 格式。"""
        chat_id = transformed["chat_id"]
        model = transformed["model"]
        
        if request.stream:
            return self._handle_stream_response(response, chat_id, model, request, transformed)
        else:
            return await self._handle_non_stream_response(response, chat_id, model)
    
    async def _handle_stream_response(
        self,
        response: httpx.Response,
        chat_id: str,
        model: str,
        request: OpenAIRequest,
        transformed: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """处理上游流式响应"""
        self.logger.info("✅ 上游响应成功，开始处理 SSE 流")

        has_tools = settings.TOOL_SUPPORT and bool(request.tools)
        buffered_content = ""
        usage_info: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        tool_calls_accum: List[Dict[str, Any]] = []
        has_sent_role = False
        finished = False
        line_count = 0

        async def ensure_role_sent() -> Optional[str]:
            nonlocal has_sent_role
            if has_sent_role:
                return None

            has_sent_role = True
            return await format_sse_chunk(
                create_openai_chunk(chat_id, model, {"role": "assistant"})
            )

        async def finalize_stream() -> AsyncGenerator[str, None]:
            nonlocal finished, tool_calls_accum
            if finished:
                return

            if has_tools and not tool_calls_accum:
                parsed_tool_calls, _ = parse_and_extract_tool_calls(buffered_content)
                normalized = self._normalize_tool_calls(parsed_tool_calls)
                if normalized:
                    tool_calls_accum = normalized
                    role_output = await ensure_role_sent()
                    if role_output:
                        yield role_output
                    for tool_call in normalized:
                        yield await format_sse_chunk(
                            create_openai_chunk(
                                chat_id,
                                model,
                                {"tool_calls": [tool_call]},
                            )
                        )

            if not has_sent_role:
                role_output = await ensure_role_sent()
                if role_output:
                    yield role_output

            finish_reason = "tool_calls" if tool_calls_accum else "stop"
            finish_chunk = create_openai_chunk(
                chat_id,
                model,
                {},
                finish_reason,
            )
            finish_chunk["usage"] = usage_info
            yield await format_sse_chunk(finish_chunk)
            yield "data: [DONE]\n\n"
            finished = True

        try:
            async for line in response.aiter_lines():
                line_count += 1
                if not line:
                    continue

                current_line = line.strip()
                if not current_line.startswith("data:"):
                    continue

                chunk_str = current_line[5:].strip()
                if not chunk_str:
                    continue

                if chunk_str == "[DONE]":
                    async for final_chunk in finalize_stream():
                        yield final_chunk
                    continue

                try:
                    chunk = json.loads(chunk_str)
                except json.JSONDecodeError as error:
                    self.logger.debug(f"❌ JSON解析错误: {error}, 内容: {chunk_str[:1000]}")
                    continue

                chunk_type = chunk.get("type")
                data = chunk.get("data", {}) if chunk_type == "chat:completion" else chunk
                if not isinstance(data, dict):
                    continue

                phase = data.get("phase")
                delta_content = data.get("delta_content", "")
                edit_content = data.get("edit_content", "")

                if phase and phase != getattr(self, "_last_phase", None):
                    self.logger.info(f"📈 SSE 阶段: {phase}")
                    self._last_phase = phase

                if data.get("usage"):
                    usage_info = data["usage"]

                if delta_content:
                    buffered_content += delta_content
                elif edit_content:
                    buffered_content += edit_content

                direct_tool_calls = self._normalize_tool_calls(
                    data.get("tool_calls"),
                    len(tool_calls_accum),
                )
                if direct_tool_calls:
                    role_output = await ensure_role_sent()
                    if role_output:
                        yield role_output
                    tool_calls_accum.extend(direct_tool_calls)
                    for tool_call in direct_tool_calls:
                        yield await format_sse_chunk(
                            create_openai_chunk(
                                chat_id,
                                model,
                                {"tool_calls": [tool_call]},
                            )
                        )

                if phase == "thinking" and delta_content:
                    cleaned = self._clean_reasoning_delta(delta_content)
                    if cleaned:
                        role_output = await ensure_role_sent()
                        if role_output:
                            yield role_output
                        yield await format_sse_chunk(
                            create_openai_chunk(
                                chat_id,
                                model,
                                {"reasoning_content": cleaned},
                            )
                        )

                elif phase == "answer":
                    text = delta_content or self._extract_answer_content(edit_content)
                    if text:
                        role_output = await ensure_role_sent()
                        if role_output:
                            yield role_output
                        yield await format_sse_chunk(
                            create_openai_chunk(
                                chat_id,
                                model,
                                {"content": text},
                            )
                        )

                elif phase == "other":
                    other_text = self._extract_answer_content(edit_content)
                    if other_text:
                        role_output = await ensure_role_sent()
                        if role_output:
                            yield role_output
                        yield await format_sse_chunk(
                            create_openai_chunk(
                                chat_id,
                                model,
                                {"content": other_text},
                            )
                        )

                elif phase == "search" or chunk_type == "web_search":
                    citation_text = self._format_search_results(data)
                    if citation_text:
                        role_output = await ensure_role_sent()
                        if role_output:
                            yield role_output
                        yield await format_sse_chunk(
                            create_openai_chunk(
                                chat_id,
                                model,
                                {"content": citation_text},
                            )
                        )

                if data.get("done"):
                    async for final_chunk in finalize_stream():
                        yield final_chunk
                    return

            self.logger.info(f"✅ SSE 流处理完成，共处理 {line_count} 行数据")

            if not finished:
                async for final_chunk in finalize_stream():
                    yield final_chunk

        except Exception as e:
            self.logger.error(f"❌ 流式响应处理错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            yield await format_sse_chunk(
                create_openai_chunk(chat_id, model, {}, "stop")
            )
            yield "data: [DONE]\n\n"
    
    async def _handle_non_stream_response(
        self, 
        response: httpx.Response, 
        chat_id: str, 
        model: str
    ) -> Dict[str, Any]:
        """处理非流式响应，聚合上游 SSE 为一次性 OpenAI 响应。"""
        final_content = ""
        reasoning_content = ""
        tool_calls_accum: List[Dict[str, Any]] = []
        usage_info: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        try:
            async for line in response.aiter_lines():
                if not line:
                    continue

                line = line.strip()
                if not line.startswith("data:"):
                    try:
                        maybe_err = json.loads(line)
                        if isinstance(maybe_err, dict) and (
                            "error" in maybe_err or "code" in maybe_err or "message" in maybe_err
                        ):
                            msg = (
                                (maybe_err.get("error") or {}).get("message")
                                if isinstance(maybe_err.get("error"), dict)
                                else maybe_err.get("message")
                            ) or "上游返回错误"
                            return handle_error(Exception(msg), "API响应")
                    except Exception:
                        pass
                    continue

                data_str = line[5:].strip()
                if not data_str or data_str in ("[DONE]", "DONE", "done"):
                    continue

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                chunk_type = chunk.get("type")
                data = chunk.get("data", {}) if chunk_type == "chat:completion" else chunk
                if not isinstance(data, dict):
                    continue

                phase = data.get("phase")
                delta_content = data.get("delta_content", "")
                edit_content = data.get("edit_content", "")

                if data.get("usage"):
                    usage_info = data["usage"]

                if phase == "thinking" and delta_content:
                    reasoning_content += self._clean_reasoning_delta(delta_content)

                elif phase == "answer":
                    if delta_content:
                        final_content += delta_content
                    elif edit_content:
                        final_content += self._extract_answer_content(edit_content)

                elif phase == "other" and edit_content:
                    final_content += self._extract_answer_content(edit_content)

                elif phase == "search" or chunk_type == "web_search":
                    final_content += self._format_search_results(data)

                tool_calls_accum.extend(
                    self._normalize_tool_calls(
                        data.get("tool_calls"),
                        len(tool_calls_accum),
                    )
                )

        except Exception as e:
            self.logger.error(f"❌ 非流式响应处理错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return handle_error(e, "非流式聚合")

        if not tool_calls_accum:
            parsed_tool_calls, cleaned_content = parse_and_extract_tool_calls(final_content)
            normalized = self._normalize_tool_calls(parsed_tool_calls)
            if normalized:
                tool_calls_accum = normalized
                final_content = cleaned_content

        final_content = (final_content or "").strip()
        reasoning_content = (reasoning_content or "").strip()

        if not final_content and reasoning_content:
            final_content = reasoning_content

        return create_openai_response_with_reasoning(
            chat_id,
            model,
            final_content,
            reasoning_content,
            usage_info,
            tool_calls_accum or None,
        )
