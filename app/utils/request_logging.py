#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""请求日志写库与流式日志包装。"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Dict, Optional

from app.services.request_log_dao import get_request_log_dao
from app.utils.logger import get_logger
from app.utils.request_source import RequestSourceInfo

logger = get_logger()


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _merge_usage(
    current: Dict[str, int],
    update: Dict[str, int],
    *,
    include_cache_in_total: bool,
) -> Dict[str, int]:
    merged = dict(current)

    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
    ):
        value = _coerce_int(update.get(key))
        if value > 0:
            merged[key] = value

    total_tokens = _coerce_int(update.get("total_tokens"))
    if total_tokens > 0:
        merged["total_tokens"] = total_tokens
        return merged

    merged["total_tokens"] = (
        merged["input_tokens"] + merged["output_tokens"]
    )
    if include_cache_in_total:
        merged["total_tokens"] += (
            merged["cache_creation_tokens"] + merged["cache_read_tokens"]
        )

    return merged


def extract_openai_usage(response: Dict[str, Any]) -> Dict[str, int]:
    """Extract usage from an OpenAI-compatible response payload."""
    usage = response.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    input_details = usage.get("input_token_details") or {}

    input_tokens = _coerce_int(
        usage.get("prompt_tokens") or usage.get("input_tokens")
    )
    output_tokens = _coerce_int(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    cache_creation_tokens = _coerce_int(
        usage.get("cache_creation_input_tokens")
        or prompt_details.get("cache_creation_tokens")
        or input_details.get("cache_creation_input_tokens")
        or input_details.get("cache_creation_tokens")
    )
    cache_read_tokens = _coerce_int(
        usage.get("cache_read_input_tokens")
        or prompt_details.get("cached_tokens")
        or prompt_details.get("cache_read_tokens")
        or input_details.get("cached_tokens")
        or input_details.get("cache_read_input_tokens")
        or input_details.get("cache_read_tokens")
    )
    total_tokens = _coerce_int(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": total_tokens,
    }


def extract_claude_usage(response: Dict[str, Any]) -> Dict[str, int]:
    """Extract usage from a Claude-compatible response payload."""
    usage = response.get("usage") or {}
    input_tokens = _coerce_int(
        usage.get("input_tokens") or usage.get("prompt_tokens")
    )
    output_tokens = _coerce_int(
        usage.get("output_tokens") or usage.get("completion_tokens")
    )
    cache_creation_tokens = _coerce_int(
        usage.get("cache_creation_input_tokens")
        or usage.get("cache_creation_tokens")
    )
    cache_read_tokens = _coerce_int(
        usage.get("cache_read_input_tokens")
        or usage.get("cached_tokens")
        or usage.get("cache_read_tokens")
    )
    total_tokens = _coerce_int(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = (
            input_tokens
            + output_tokens
            + cache_creation_tokens
            + cache_read_tokens
        )

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": total_tokens,
    }


async def write_request_log(
    *,
    provider: str,
    model: str,
    source_info: RequestSourceInfo,
    success: bool,
    started_at: float,
    status_code: int = 200,
    first_token_time: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    total_tokens: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Persist a request log entry without breaking request handling."""
    duration = max(0.0, time.perf_counter() - started_at)
    try:
        dao = get_request_log_dao()
        await dao.add_log(
            provider=provider,
            endpoint=source_info.endpoint,
            source=source_info.source,
            protocol=source_info.protocol,
            client_name=source_info.client_name,
            model=model,
            status_code=status_code,
            success=success,
            duration=duration,
            first_token_time=first_token_time,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            total_tokens=total_tokens,
            error_message=error_message,
        )
    except Exception as exc:
        logger.error(f"写入请求日志失败: {exc}")


def _openai_payload_has_output(payload: Dict[str, Any]) -> bool:
    choice = ((payload.get("choices") or [{}])[0]) if isinstance(payload, dict) else {}
    delta = choice.get("delta") or {}
    return bool(
        delta.get("content")
        or delta.get("reasoning_content")
        or delta.get("tool_calls")
    )


async def wrap_openai_stream_with_logging(
    stream: AsyncGenerator[str, None],
    *,
    provider: str,
    model: str,
    source_info: RequestSourceInfo,
    started_at: float,
) -> AsyncGenerator[str, None]:
    """Wrap OpenAI SSE stream and persist completion metadata."""
    success = True
    status_code = 200
    error_message: Optional[str] = None
    first_token_time = 0.0
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
    }

    try:
        async for chunk in stream:
            if chunk.startswith("data: "):
                payload_text = chunk[6:].strip()
                if payload_text and payload_text != "[DONE]":
                    try:
                        payload = json.loads(payload_text)
                    except json.JSONDecodeError:
                        payload = None

                    if isinstance(payload, dict):
                        if "error" in payload:
                            success = False
                            error = payload.get("error") or {}
                            error_message = (
                                error.get("message")
                                or "Unknown stream error"
                            )
                            status_code = int(error.get("code") or 500)
                        else:
                            if (
                                not first_token_time
                                and _openai_payload_has_output(payload)
                            ):
                                first_token_time = max(
                                    0.0,
                                    time.perf_counter() - started_at,
                                )
                            if payload.get("usage"):
                                usage = _merge_usage(
                                    usage,
                                    extract_openai_usage(payload),
                                    include_cache_in_total=False,
                                )

            yield chunk
    except Exception as exc:
        success = False
        status_code = 500
        error_message = str(exc)
        raise
    finally:
        await write_request_log(
            provider=provider,
            model=model,
            source_info=source_info,
            success=success,
            started_at=started_at,
            status_code=status_code,
            first_token_time=first_token_time,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_tokens=usage["cache_creation_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            total_tokens=usage["total_tokens"],
            error_message=error_message,
        )


async def wrap_claude_stream_with_logging(
    stream: AsyncGenerator[str, None],
    *,
    provider: str,
    model: str,
    source_info: RequestSourceInfo,
    started_at: float,
    input_tokens: int,
) -> AsyncGenerator[str, None]:
    """Wrap Claude SSE stream and persist completion metadata."""
    success = True
    status_code = 200
    error_message: Optional[str] = None
    first_token_time = 0.0
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": input_tokens,
    }
    current_event: Optional[str] = None

    try:
        async for chunk in stream:
            if chunk.startswith("event: "):
                current_event = chunk[7:].strip()
            elif chunk.startswith("data: "):
                payload_text = chunk[6:].strip()
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    payload = None

                if isinstance(payload, dict):
                    if current_event == "content_block_delta" and not first_token_time:
                        first_token_time = max(0.0, time.perf_counter() - started_at)
                    if payload.get("usage"):
                        usage = _merge_usage(
                            usage,
                            extract_claude_usage(payload),
                            include_cache_in_total=True,
                        )
                    elif current_event == "error":
                        success = False
                        status_code = 500
                        error = payload.get("error") or {}
                        error_message = error.get("message") or "Claude stream error"

            yield chunk
    except Exception as exc:
        success = False
        status_code = 500
        error_message = str(exc)
        raise
    finally:
        await write_request_log(
            provider=provider,
            model=model,
            source_info=source_info,
            success=success,
            started_at=started_at,
            status_code=status_code,
            first_token_time=first_token_time,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_tokens=usage["cache_creation_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            total_tokens=usage["total_tokens"],
            error_message=error_message,
        )
