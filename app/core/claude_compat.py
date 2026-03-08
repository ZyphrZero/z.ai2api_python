#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Claude Messages API 兼容辅助函数。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional


def extract_text(content: Any) -> str:
    """Extract plain text from Claude/OpenAI mixed content blocks."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return " ".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()

    return str(content) if content else ""


def claude_messages_to_openai(system: Any, messages: list[dict]) -> list[dict]:
    """Convert Claude messages payload into OpenAI-style messages."""
    converted: list[dict] = []

    if system:
        if isinstance(system, str):
            converted.append({"role": "system", "content": system})
        elif isinstance(system, list):
            system_text = [
                block.get("text", "")
                for block in system
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if system_text:
                converted.append({
                    "role": "system",
                    "content": "\n".join(system_text),
                })

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        if role == "assistant" and isinstance(content, list):
            text_parts: list[str] = []
            tool_calls: list[dict] = []

            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get(
                                "id",
                                f"call_{uuid.uuid4().hex[:24]}",
                            ),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(
                                    block.get("input", {}),
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    )

            openai_message: dict = {
                "role": "assistant",
                "content": " ".join(text_parts).strip() or None,
            }
            if tool_calls:
                openai_message["tool_calls"] = tool_calls
            converted.append(openai_message)
            continue

        if role == "user" and isinstance(content, list):
            has_tool_result = any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
            if has_tool_result:
                for block in content:
                    if not isinstance(block, dict):
                        continue

                    block_type = block.get("type")
                    if block_type == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, str):
                            rendered = result_content
                        elif isinstance(result_content, list):
                            rendered = " ".join(
                                item.get("text", "")
                                for item in result_content
                                if isinstance(item, dict)
                                and item.get("type") == "text"
                            )
                        else:
                            rendered = str(result_content)

                        converted.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": rendered,
                            }
                        )
                    elif block_type == "text":
                        converted.append(
                            {"role": "user", "content": block.get("text", "")}
                        )
                continue

        converted.append({"role": role, "content": extract_text(content)})

    return converted


def claude_tools_to_openai(tools: Optional[list[dict]]) -> Optional[list[dict]]:
    """Convert Claude tool schemas into OpenAI function tools."""
    if not tools:
        return None

    converted = [
        {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        for tool in tools
        if isinstance(tool, dict)
    ]
    return converted or None


def claude_tool_choice_to_openai(tool_choice: Any) -> Any:
    """Convert Claude tool_choice payload into OpenAI-compatible form."""
    if not isinstance(tool_choice, dict):
        return tool_choice

    tool_choice_type = tool_choice.get("type", "auto")
    if tool_choice_type == "auto":
        return "auto"
    if tool_choice_type == "any":
        return "required"
    if tool_choice_type == "none":
        return "none"
    if tool_choice_type == "tool":
        name = tool_choice.get("name", "")
        if name:
            return {"type": "function", "function": {"name": name}}
    return tool_choice


def make_claude_id() -> str:
    """Generate a Claude-style message id."""
    return f"msg_{uuid.uuid4().hex[:24]}"


def build_tool_call_blocks(tool_calls: list[dict]) -> list[dict]:
    """Convert OpenAI tool calls to Claude tool_use blocks."""
    blocks = []
    for tool_call in tool_calls:
        function_data = (
            tool_call.get("function")
            if isinstance(tool_call.get("function"), dict)
            else {}
        )
        arguments = function_data.get("arguments", "{}")
        try:
            input_data = json.loads(arguments) if isinstance(arguments, str) else arguments
        except Exception:
            input_data = {}

        blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get(
                    "id",
                    f"toolu_{uuid.uuid4().hex[:20]}",
                ).replace("call_", "toolu_"),
                "name": function_data.get("name", ""),
                "input": input_data,
            }
        )
    return blocks


def build_non_stream_response(
    msg_id: str,
    model: str,
    reasoning_parts: list[str],
    answer_text: str,
    tool_calls: Optional[list[dict]],
    input_tokens: int,
    output_tokens: int,
) -> dict:
    """Build a Claude non-streaming message response."""
    content: list[dict] = []
    if reasoning_parts:
        content.append(
            {"type": "thinking", "thinking": "".join(reasoning_parts)}
        )
    if answer_text:
        content.append({"type": "text", "text": answer_text})
    elif not tool_calls:
        content.append({"type": "text", "text": ""})
    if tool_calls:
        content.extend(build_tool_call_blocks(tool_calls))

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": "tool_use" if tool_calls else "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def sse(event: str, data: dict) -> str:
    """Format a Claude SSE event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_message_start(msg_id: str, model: str, input_tokens: int) -> str:
    """Create Claude message_start SSE event."""
    return sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 0,
                },
            },
        },
    )


def sse_ping() -> str:
    """Create Claude ping SSE event."""
    return sse("ping", {"type": "ping"})


def sse_content_block_start(index: int, block: dict) -> str:
    """Create Claude content_block_start SSE event."""
    return sse(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": block,
        },
    )


def sse_content_block_delta(index: int, delta: dict) -> str:
    """Create Claude content_block_delta SSE event."""
    return sse(
        "content_block_delta",
        {"type": "content_block_delta", "index": index, "delta": delta},
    )


def sse_content_block_stop(index: int) -> str:
    """Create Claude content_block_stop SSE event."""
    return sse(
        "content_block_stop",
        {"type": "content_block_stop", "index": index},
    )


def sse_message_delta(stop_reason: str, output_tokens: int) -> str:
    """Create Claude message_delta SSE event."""
    return sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )


def sse_message_stop() -> str:
    """Create Claude message_stop SSE event."""
    return sse("message_stop", {"type": "message_stop"})


def sse_error(error_type: str, message: str) -> str:
    """Create Claude error SSE event."""
    return sse(
        "error",
        {
            "type": "error",
            "error": {"type": error_type, "message": message},
        },
    )
