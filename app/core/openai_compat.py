#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""OpenAI 兼容响应辅助函数。"""

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger()
SYSTEM_FINGERPRINT = "fp_api_proxy_001"


def create_chat_id() -> str:
    """生成聊天 ID。"""
    return f"chatcmpl-{uuid.uuid4().hex}"


def create_openai_chunk(
    chat_id: str,
    model: str,
    delta: Dict[str, Any],
    finish_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """创建 OpenAI 格式的流式响应块。"""
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "system_fingerprint": SYSTEM_FINGERPRINT,
    }


def create_openai_response(
    chat_id: str,
    model: str,
    content: str,
    usage: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """创建 OpenAI 格式的非流式响应。"""
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "system_fingerprint": SYSTEM_FINGERPRINT,
    }


def create_openai_response_with_reasoning(
    chat_id: str,
    model: str,
    content: str,
    reasoning_content: Optional[str] = None,
    usage: Optional[Dict[str, int]] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """创建包含 reasoning/tool_calls 的 OpenAI 响应。"""
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }

    if reasoning_content and reasoning_content.strip():
        message["reasoning_content"] = reasoning_content

    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "logprobs": None,
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "system_fingerprint": SYSTEM_FINGERPRINT,
    }


async def format_sse_chunk(chunk: Dict[str, Any]) -> str:
    """格式化 SSE 响应块。"""
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def format_sse_done() -> str:
    """格式化 SSE 结束标记。"""
    return "data: [DONE]\n\n"


def handle_error(error: Exception, context: str = "") -> Dict[str, Any]:
    """统一错误处理。"""
    error_msg = f"上游{context}错误: {str(error)}" if context else f"上游错误: {str(error)}"
    logger.error(error_msg)
    return {
        "error": {
            "message": error_msg,
            "type": "upstream_error",
            "code": "internal_error",
        }
    }
