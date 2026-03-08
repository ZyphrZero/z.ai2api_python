#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import math
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.claude_compat import (
    build_non_stream_response,
    claude_messages_to_openai,
    claude_tool_choice_to_openai,
    claude_tools_to_openai,
    extract_text,
    make_claude_id,
    sse_content_block_delta,
    sse_content_block_start,
    sse_content_block_stop,
    sse_error,
    sse_message_delta,
    sse_message_start,
    sse_message_stop,
    sse_ping,
)
from app.core.config import settings
from app.core.openai import get_upstream_client
from app.models.schemas import Message, OpenAIRequest
from app.utils.logger import get_logger
from app.utils.request_logging import (
    extract_claude_usage,
    wrap_claude_stream_with_logging,
    write_request_log,
)
from app.utils.request_source import detect_request_source, format_request_source

logger = get_logger()
router = APIRouter()


def _resolve_claude_model(model: Any) -> str:
    """Map Claude/Claude Code model aliases to local upstream-supported models."""
    if not isinstance(model, str) or not model.strip():
        return settings.GLM5_MODEL

    raw_model = model.strip()
    normalized = raw_model.casefold()
    if normalized.endswith("[1m]"):
        normalized = normalized[:-4].rstrip()

    direct_models = {
        settings.GLM45_MODEL.casefold(): settings.GLM45_MODEL,
        settings.GLM45_THINKING_MODEL.casefold(): settings.GLM45_THINKING_MODEL,
        settings.GLM45_SEARCH_MODEL.casefold(): settings.GLM45_SEARCH_MODEL,
        settings.GLM45_AIR_MODEL.casefold(): settings.GLM45_AIR_MODEL,
        settings.GLM46V_MODEL.casefold(): settings.GLM46V_MODEL,
        settings.GLM5_MODEL.casefold(): settings.GLM5_MODEL,
        settings.GLM47_MODEL.casefold(): settings.GLM47_MODEL,
        settings.GLM47_THINKING_MODEL.casefold(): settings.GLM47_THINKING_MODEL,
        settings.GLM47_SEARCH_MODEL.casefold(): settings.GLM47_SEARCH_MODEL,
        settings.GLM47_ADVANCED_SEARCH_MODEL.casefold(): settings.GLM47_ADVANCED_SEARCH_MODEL,
    }
    if normalized in direct_models:
        return direct_models[normalized]

    alias_map = {
        "default": settings.GLM5_MODEL,
        "sonnet": settings.GLM5_MODEL,
        "haiku": settings.GLM45_AIR_MODEL,
        "opus": settings.GLM5_MODEL,
        "opusplan": settings.GLM47_THINKING_MODEL,
    }
    if normalized in alias_map:
        return alias_map[normalized]

    if normalized.startswith("claude-sonnet") or normalized.startswith("claude-3-7-sonnet") or normalized.startswith("claude-3-5-sonnet"):
        return settings.GLM5_MODEL
    if normalized.startswith("claude-opus") or normalized.startswith("claude-4-opus"):
        return settings.GLM5_MODEL
    if normalized.startswith("claude-haiku") or normalized.startswith("claude-3-5-haiku"):
        return settings.GLM45_AIR_MODEL

    return raw_model


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 2))


def _extract_api_key(
    authorization: Optional[str],
    x_api_key: Optional[str],
) -> Optional[str]:
    if x_api_key:
        return x_api_key
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


def _claude_error_response(
    message: str,
    status_code: int,
    error_type: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "error",
            "error": {"type": error_type, "message": message},
        },
    )


def _build_openai_request(body: Dict[str, Any]) -> OpenAIRequest:
    system = body.get("system")
    claude_messages = body.get("messages", [])
    openai_messages = claude_messages_to_openai(system, claude_messages)
    openai_tools = claude_tools_to_openai(body.get("tools"))
    tool_choice = claude_tool_choice_to_openai(body.get("tool_choice"))

    thinking = body.get("thinking")
    enable_thinking = None
    if isinstance(thinking, dict):
        thinking_type = thinking.get("type")
        if thinking_type == "enabled":
            enable_thinking = True
        elif thinking_type == "disabled":
            enable_thinking = False

    messages = [Message.model_validate(message) for message in openai_messages]
    resolved_model = _resolve_claude_model(body.get("model", settings.GLM5_MODEL))
    if resolved_model != body.get("model", settings.GLM5_MODEL):
        logger.info(
            f"🔀 Claude 模型映射: "
            f"{body.get('model', settings.GLM5_MODEL)} -> {resolved_model}"
        )

    return OpenAIRequest(
        model=resolved_model,
        messages=messages,
        stream=bool(body.get("stream", False)),
        temperature=body.get("temperature"),
        max_tokens=body.get("max_tokens"),
        tools=openai_tools,
        tool_choice=tool_choice,
        enable_thinking=enable_thinking,
    )


def _build_prompt_text(body: Dict[str, Any]) -> str:
    prompt_parts: List[str] = []
    system = body.get("system")
    if system:
        prompt_parts.append(extract_text(system))

    for message in body.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        text = extract_text(content)
        if text:
            prompt_parts.append(text)

    return "\n".join(part for part in prompt_parts if part)


def _normalize_tool_calls(tool_calls: Any) -> List[Dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen_ids = set()
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue

        tool_call_id = tool_call.get("id") or f"call_{uuid.uuid4().hex[:24]}"
        if tool_call_id in seen_ids:
            continue
        seen_ids.add(tool_call_id)

        function_data = (
            tool_call.get("function")
            if isinstance(tool_call.get("function"), dict)
            else {}
        )
        arguments = function_data.get("arguments", "{}")
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments, ensure_ascii=False)
            except Exception:
                arguments = "{}"

        normalized.append(
            {
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": function_data.get("name", ""),
                    "arguments": arguments,
                },
            }
        )

    return normalized


def _convert_openai_response_to_claude(response: Dict[str, Any], msg_id: str) -> Dict[str, Any]:
    choice = ((response.get("choices") or [{}])[0]) if isinstance(response, dict) else {}
    message = choice.get("message") or {}
    reasoning = message.get("reasoning_content")
    usage = response.get("usage") or {}
    return build_non_stream_response(
        msg_id=msg_id,
        model=response.get("model", settings.GLM5_MODEL),
        reasoning_parts=[reasoning] if isinstance(reasoning, str) and reasoning else [],
        answer_text=message.get("content") or "",
        tool_calls=_normalize_tool_calls(message.get("tool_calls")),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )


async def _stream_openai_to_claude(
    openai_stream: AsyncGenerator[str, None],
    msg_id: str,
    model: str,
    input_tokens: int,
) -> AsyncGenerator[str, None]:
    reasoning_parts: List[str] = []
    answer_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    block_index = 0
    thinking_started = False
    final_output_tokens = 0

    yield sse_message_start(msg_id, model, input_tokens)
    yield sse_ping()

    try:
        async for chunk in openai_stream:
            if not chunk.startswith("data: "):
                continue

            payload_text = chunk[6:].strip()
            if not payload_text or payload_text == "[DONE]":
                continue

            payload = json.loads(payload_text)
            if isinstance(payload, dict) and "error" in payload:
                error = payload.get("error") or {}
                yield sse_error(
                    error.get("type", "api_error"),
                    error.get("message", "Upstream error"),
                )
                return

            choice = ((payload.get("choices") or [{}])[0]) if isinstance(payload, dict) else {}
            delta = choice.get("delta") or {}

            reasoning_delta = delta.get("reasoning_content")
            if reasoning_delta:
                if not thinking_started:
                    yield sse_content_block_start(
                        block_index,
                        {"type": "thinking", "thinking": ""},
                    )
                    thinking_started = True

                reasoning_parts.append(reasoning_delta)
                yield sse_content_block_delta(
                    block_index,
                    {"type": "thinking_delta", "thinking": reasoning_delta},
                )

            content_delta = delta.get("content")
            if content_delta:
                answer_parts.append(content_delta)

            if payload.get("usage"):
                usage = payload.get("usage") or {}
                final_output_tokens = int(
                    usage.get("completion_tokens") or final_output_tokens
                )

            tool_calls.extend(_normalize_tool_calls(delta.get("tool_calls")))

        if thinking_started:
            yield sse_content_block_stop(block_index)
            block_index += 1

        answer_text = "".join(answer_parts)
        if answer_text:
            yield sse_content_block_start(block_index, {"type": "text", "text": ""})
            yield sse_content_block_delta(
                block_index,
                {"type": "text_delta", "text": answer_text},
            )
            yield sse_content_block_stop(block_index)
            block_index += 1

        if tool_calls:
            for tool_call in tool_calls:
                function_data = tool_call.get("function") or {}
                tool_id = tool_call.get(
                    "id",
                    f"toolu_{uuid.uuid4().hex[:20]}",
                ).replace("call_", "toolu_")
                yield sse_content_block_start(
                    block_index,
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": function_data.get("name", ""),
                        "input": {},
                    },
                )
                yield sse_content_block_delta(
                    block_index,
                    {
                        "type": "input_json_delta",
                        "partial_json": function_data.get("arguments", "{}"),
                    },
                )
                yield sse_content_block_stop(block_index)
                block_index += 1

        if not final_output_tokens:
            final_output_tokens = _estimate_tokens(
                "".join(reasoning_parts) + answer_text
            )

        yield sse_message_delta(
            "tool_use" if tool_calls else "end_turn",
            final_output_tokens,
        )
        yield sse_message_stop()
    except Exception as exc:
        logger.error(f"❌ Claude 流式响应转换失败: {exc}")
        yield sse_error("api_error", str(exc))


@router.post("/v1/messages")
@router.post("/anthropic/v1/messages")
async def claude_messages(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
):
    try:
        body = await request.json()
    except Exception:
        return _claude_error_response(
            "Invalid JSON body",
            400,
            "invalid_request_error",
        )

    if not settings.SKIP_AUTH_TOKEN:
        api_key = _extract_api_key(authorization, x_api_key)
        if not api_key:
            return _claude_error_response(
                "Missing API key",
                401,
                "authentication_error",
            )
        if api_key != settings.AUTH_TOKEN:
            return _claude_error_response(
                "Invalid API key",
                401,
                "authentication_error",
            )

    try:
        openai_request = _build_openai_request(body)
    except Exception as exc:
        return _claude_error_response(
            f"Invalid request: {exc}",
            400,
            "invalid_request_error",
        )

    if not openai_request.messages:
        return _claude_error_response(
            "messages is required",
            400,
            "invalid_request_error",
        )

    source_info = detect_request_source(
        request,
        protocol_hint="anthropic",
        model_hint=body.get("model"),
    )
    source_prefix = format_request_source(source_info)
    started_at = time.perf_counter()
    logger.info(
        f"{source_prefix} 🤖 收到 Claude 请求 - 模型: {body.get('model')}, 映射模型: {openai_request.model}, 流式: {openai_request.stream}, 消息数: {len(openai_request.messages)}, 工具数: {len(openai_request.tools) if openai_request.tools else 0}"
    )

    msg_id = make_claude_id()
    input_tokens = _estimate_tokens(_build_prompt_text(body))

    try:
        client = get_upstream_client()
        result = await client.chat_completion(openai_request)
    except Exception as exc:
        logger.error(f"{source_prefix} ❌ Claude 请求处理失败: {exc}")
        await write_request_log(
            provider="zai",
            model=openai_request.model,
            source_info=source_info,
            success=False,
            started_at=started_at,
            status_code=500,
            error_message=str(exc),
        )
        return _claude_error_response(str(exc), 500, "api_error")

    if isinstance(result, dict) and "error" in result:
        error = result.get("error") or {}
        return _claude_error_response(
            error.get("message", "Unknown upstream error"),
            500,
            error.get("type", "api_error"),
        )

    if openai_request.stream:
        if not hasattr(result, "__aiter__"):
            return _claude_error_response(
                "Expected streaming response",
                500,
                "api_error",
            )

        return StreamingResponse(
            wrap_claude_stream_with_logging(
                _stream_openai_to_claude(
                    result,
                    msg_id,
                    openai_request.model,
                    input_tokens,
                ),
                provider="zai",
                model=openai_request.model,
                source_info=source_info,
                started_at=started_at,
                input_tokens=input_tokens,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )

    if not isinstance(result, dict):
        return _claude_error_response(
            "Expected non-streaming response payload",
            500,
            "api_error",
        )

    response_data = _convert_openai_response_to_claude(result, msg_id)
    if not response_data.get("usage", {}).get("input_tokens"):
        response_data["usage"]["input_tokens"] = input_tokens
    usage = extract_claude_usage(response_data)
    await write_request_log(
        provider="zai",
        model=openai_request.model,
        source_info=source_info,
        success=True,
        started_at=started_at,
        status_code=200,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )
    return JSONResponse(content=response_data)
