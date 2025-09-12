#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import json
from datetime import datetime
from typing import List, Dict, Any
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
import httpx

from app.core.config import settings
from app.models.schemas import OpenAIRequest, Message, ModelsResponse, Model
from app.utils.logger import get_logger
from app.core.zai_transformer import ZAITransformer, generate_uuid
from app.utils.sse_tool_handler import SSEToolHandler

logger = get_logger()

router = APIRouter()

# 全局转换器实例
transformer = ZAITransformer()


@router.get("/v1/models")
async def list_models():
    """List available models"""
    current_time = int(time.time())
    response = ModelsResponse(
        data=[
            Model(id=settings.PRIMARY_MODEL, created=current_time, owned_by="z.ai"),
            Model(id=settings.THINKING_MODEL, created=current_time, owned_by="z.ai"),
            Model(id=settings.SEARCH_MODEL, created=current_time, owned_by="z.ai"),
            Model(id=settings.AIR_MODEL, created=current_time, owned_by="z.ai"),
        ]
    )
    return response


@router.post("/v1/chat/completions")
async def chat_completions(request: OpenAIRequest, authorization: str = Header(...)):
    """Handle chat completion requests with ZAI transformer"""
    logger.info(f"📥 收到 OpenAI 请求 - 模型: {request.model}, 流式: {request.stream}")
    logger.debug(f"请求详情 - 消息数: {len(request.messages)}, 工具数: {len(request.tools) if request.tools else 0}")
    
    # 输出消息内容用于调试
    for idx, msg in enumerate(request.messages):
        content_preview = str(msg.content)[:1000] if msg.content else "None"
        logger.debug(f"  消息[{idx}] - 角色: {msg.role}, 内容预览: {content_preview}...")

    try:
        # Validate API key (skip if SKIP_AUTH_TOKEN is enabled)
        if not settings.SKIP_AUTH_TOKEN:
            if not authorization.startswith("Bearer "):
                logger.debug("缺少或无效的Authorization头")
                raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

            api_key = authorization[7:]
            if api_key != settings.AUTH_TOKEN:
                logger.debug(f"无效的API key: {api_key}")
                raise HTTPException(status_code=401, detail="Invalid API key")

            logger.debug(f"API key验证通过")
        else:
            logger.debug("SKIP_AUTH_TOKEN已启用，跳过API key验证")

        # 输出原始请求体用于调试
        request_dict = request.model_dump()
        # logger.debug(f"🔄 原始 OpenAI 请求体: {json.dumps(request_dict, ensure_ascii=False, indent=2)}")
        
        # 使用新的转换器转换请求
        logger.info("🔄 开始转换请求格式: OpenAI -> Z.AI")
        transformed = await transformer.transform_request_in(request_dict)

        logger.info(
            f"✅ 请求转换完成 - 上游模型: {transformed['body']['model']}, "
            f"chat_id: {transformed['body']['chat_id']}"
        )
        logger.debug(
            f"  特性配置 - enable_thinking: {transformed['body']['features']['enable_thinking']}, "
            f"web_search: {transformed['body']['features']['web_search']}, "
            f"mcp_servers: {transformed['body'].get('mcp_servers', [])}"
        )
        # logger.debug(f"🔄 转换后 Z.AI 请求体: {json.dumps(transformed['body'], ensure_ascii=False, indent=2)}")

        # 调用上游API
        async def stream_response():
            """流式响应生成器"""
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    # 发送请求到上游
                    logger.info(f"🎯 发送请求到 Z.AI: {transformed['config']['url']}")
                    logger.debug(f"  请求头数量: {len(transformed['config']['headers'])}")
                    
                    async with client.stream(
                        "POST",
                        transformed["config"]["url"],
                        json=transformed["body"],
                        headers=transformed["config"]["headers"],
                    ) as response:
                        if response.status_code != 200:
                            logger.error(f"❌ 上游返回错误: {response.status_code}")
                            error_text = await response.aread()
                            logger.error(f"错误详情: {error_text.decode('utf-8', errors='ignore')}")
                            yield f"data: {json.dumps({'error': 'Upstream error'})}\n\n"
                            return
                        
                        logger.info(f"✅ Z.AI 响应成功，开始处理 SSE 流")

                        # 初始化工具处理器（如果需要）
                        has_tools = transformed["body"].get("tools") is not None
                        tool_handler = None
                        if has_tools:
                            chat_id = transformed["body"]["chat_id"]
                            model = request.model
                            tool_handler = SSEToolHandler(chat_id, model)
                            logger.info(f"🔧 初始化工具处理器 - chat_id: {chat_id}, 工具数: {len(transformed['body'].get('tools', []))}")

                        # 处理状态
                        has_thinking = False
                        thinking_signature = None

                        # 处理SSE流
                        buffer = ""
                        line_count = 0
                        logger.debug("📡 开始接收 SSE 流数据...")
                        
                        async for line in response.aiter_lines():
                            line_count += 1
                            if not line:
                                # logger.debug(f"  行[{line_count}]: 空行，跳过")
                                continue

                            logger.debug(f"  行[{line_count}]: 接收到数据 - {line[:1000]}..." if len(line) > 1000 else f"  行[{line_count}]: 接收到数据 - {line}")
                            
                            # 累积到buffer处理完整的数据行
                            buffer += line + "\n"

                            # 检查是否有完整的data行
                            while "\n" in buffer:
                                current_line, buffer = buffer.split("\n", 1)
                                if not current_line.strip():
                                    continue

                                if current_line.startswith("data:"):
                                    chunk_str = current_line[5:].strip()
                                    if not chunk_str or chunk_str == "[DONE]":
                                        if chunk_str == "[DONE]":
                                            logger.debug("🏁 收到结束信号 [DONE]")
                                            yield "data: [DONE]\n\n"
                                        continue
                                    
                                    logger.debug(f"  📦 解析数据块: {chunk_str[:1000]}..." if len(chunk_str) > 1000 else f"  📦 解析数据块: {chunk_str}")

                                    try:
                                        chunk = json.loads(chunk_str)

                                        if chunk.get("type") == "chat:completion":
                                            data = chunk.get("data", {})
                                            phase = data.get("phase")
                                            
                                            # 记录每个阶段（只在阶段变化时记录）
                                            if phase and phase != getattr(stream_response, '_last_phase', None):
                                                logger.info(f"📈 SSE 阶段变化: {getattr(stream_response, '_last_phase', 'None')} -> {phase}")
                                                stream_response._last_phase = phase

                                            # 处理工具调用
                                            if phase == "tool_call" and tool_handler:
                                                for output in tool_handler.process_tool_call_phase(data, True):
                                                    yield output

                                            # 处理其他阶段（工具结束）
                                            elif phase == "other" and tool_handler:
                                                for output in tool_handler.process_other_phase(data, True):
                                                    yield output

                                            # 处理思考内容
                                            elif phase == "thinking":
                                                if not has_thinking:
                                                    has_thinking = True
                                                    # 发送初始角色
                                                    role_chunk = {
                                                        "choices": [
                                                            {
                                                                "delta": {"role": "assistant"},
                                                                "finish_reason": None,
                                                                "index": 0,
                                                                "logprobs": None,
                                                            }
                                                        ],
                                                        "created": int(time.time()),
                                                        "id": transformed["body"]["chat_id"],
                                                        "model": request.model,
                                                        "object": "chat.completion.chunk",
                                                        "system_fingerprint": "fp_zai_001",
                                                    }
                                                    logger.debug("    ➡️ 发送初始角色")
                                                    yield f"data: {json.dumps(role_chunk)}\n\n"

                                                delta_content = data.get("delta_content", "")
                                                if delta_content:
                                                    # 处理思考内容格式
                                                    if delta_content.startswith("<details"):
                                                        content = (
                                                            delta_content.split("</summary>\n>")[-1].strip()
                                                            if "</summary>\n>" in delta_content
                                                            else delta_content
                                                        )
                                                    else:
                                                        content = delta_content

                                                    thinking_chunk = {
                                                        "choices": [
                                                            {
                                                                "delta": {
                                                                    "role": "assistant",
                                                                    "thinking": {"content": content},
                                                                },
                                                                "finish_reason": None,
                                                                "index": 0,
                                                                "logprobs": None,
                                                            }
                                                        ],
                                                        "created": int(time.time()),
                                                        "id": transformed["body"]["chat_id"],
                                                        "model": request.model,
                                                        "object": "chat.completion.chunk",
                                                        "system_fingerprint": "fp_zai_001",
                                                    }
                                                    yield f"data: {json.dumps(thinking_chunk)}\n\n"

                                            # 处理答案内容
                                            elif phase == "answer":
                                                edit_content = data.get("edit_content", "")
                                                delta_content = data.get("delta_content", "")

                                                # 处理思考结束和答案开始
                                                if edit_content and "</details>\n" in edit_content:
                                                    if has_thinking:
                                                        # 发送思考签名
                                                        thinking_signature = str(int(time.time() * 1000))
                                                        sig_chunk = {
                                                            "choices": [
                                                                {
                                                                    "delta": {
                                                                        "role": "assistant",
                                                                        "thinking": {
                                                                            "content": "",
                                                                            "signature": thinking_signature,
                                                                        },
                                                                    },
                                                                    "finish_reason": None,
                                                                    "index": 0,
                                                                    "logprobs": None,
                                                                }
                                                            ],
                                                            "created": int(time.time()),
                                                            "id": transformed["body"]["chat_id"],
                                                            "model": request.model,
                                                            "object": "chat.completion.chunk",
                                                            "system_fingerprint": "fp_zai_001",
                                                        }
                                                        yield f"data: {json.dumps(sig_chunk)}\n\n"

                                                    # 提取答案内容
                                                    content_after = edit_content.split("</details>\n")[-1]
                                                    if content_after:
                                                        content_chunk = {
                                                            "choices": [
                                                                {
                                                                    "delta": {
                                                                        "role": "assistant",
                                                                        "content": content_after,
                                                                    },
                                                                    "finish_reason": None,
                                                                    "index": 0,
                                                                    "logprobs": None,
                                                                }
                                                            ],
                                                            "created": int(time.time()),
                                                            "id": transformed["body"]["chat_id"],
                                                            "model": request.model,
                                                            "object": "chat.completion.chunk",
                                                            "system_fingerprint": "fp_zai_001",
                                                        }
                                                        yield f"data: {json.dumps(content_chunk)}\n\n"

                                                # 处理增量内容
                                                elif delta_content:
                                                    logger.debug(f"    📝 答案内容片段: {delta_content[:1000]}...")
                                                    # 如果还没有发送角色
                                                    if not has_thinking:
                                                        role_chunk = {
                                                            "choices": [
                                                                {
                                                                    "delta": {"role": "assistant"},
                                                                    "finish_reason": None,
                                                                    "index": 0,
                                                                    "logprobs": None,
                                                                }
                                                            ],
                                                            "created": int(time.time()),
                                                            "id": transformed["body"]["chat_id"],
                                                            "model": request.model,
                                                            "object": "chat.completion.chunk",
                                                            "system_fingerprint": "fp_zai_001",
                                                        }
                                                        yield f"data: {json.dumps(role_chunk)}\n\n"

                                                    content_chunk = {
                                                        "choices": [
                                                            {
                                                                "delta": {
                                                                    "role": "assistant",
                                                                    "content": delta_content,
                                                                },
                                                                "finish_reason": None,
                                                                "index": 0,
                                                                "logprobs": None,
                                                            }
                                                        ],
                                                        "created": int(time.time()),
                                                        "id": transformed["body"]["chat_id"],
                                                        "model": request.model,
                                                        "object": "chat.completion.chunk",
                                                        "system_fingerprint": "fp_zai_001",
                                                    }
                                                    output_data = f"data: {json.dumps(content_chunk)}\n\n"
                                                    logger.debug(f"    ➡️ 输出内容块到客户端: {output_data[:1000]}...")
                                                    yield output_data

                                                # 处理完成
                                                if data.get("usage"):
                                                    logger.info(f"📦 完成响应 - 使用统计: {json.dumps(data['usage'])}")
                                                    
                                                    # 只有在非工具调用模式下才发送普通完成信号
                                                    if not tool_handler or not tool_handler.has_tool_call:
                                                        finish_chunk = {
                                                            "choices": [
                                                                {
                                                                    "delta": {"role": "assistant", "content": ""},
                                                                    "finish_reason": "stop",
                                                                    "index": 0,
                                                                    "logprobs": None,
                                                                }
                                                            ],
                                                            "usage": data["usage"],
                                                            "created": int(time.time()),
                                                            "id": transformed["body"]["chat_id"],
                                                            "model": request.model,
                                                            "object": "chat.completion.chunk",
                                                            "system_fingerprint": "fp_zai_001",
                                                        }
                                                        finish_output = f"data: {json.dumps(finish_chunk)}\n\n"
                                                        logger.debug(f"    ➡️ 发送完成信号: {finish_output[:1000]}...")
                                                        yield finish_output
                                                        logger.debug("    ➡️ 发送 [DONE]")
                                                        yield "data: [DONE]\n\n"

                                    except json.JSONDecodeError as e:
                                        logger.debug(f"JSON解析错误: {e}, 内容: {chunk_str[:1000]}")
                                    except Exception as e:
                                        logger.error(f"处理chunk错误: {e}")

                        # 确保发送结束信号
                        if not tool_handler or not tool_handler.has_tool_call:
                            logger.debug("📤 发送最终 [DONE] 信号")
                            yield "data: [DONE]\n\n"
                        
                        logger.info(f"✅ SSE 流处理完成，共处理 {line_count} 行数据")

            except Exception as e:
                logger.error(f"流处理错误: {e}")
                import traceback

                logger.error(traceback.format_exc())
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        # 返回流式响应
        logger.info("🚀 启动 SSE 流式响应")
        
        # 创建一个包装的生成器来追踪数据流
        async def logged_stream():
            chunk_count = 0
            try:
                logger.debug("📤 开始向客户端流式传输数据...")
                async for chunk in stream_response():
                    chunk_count += 1
                    logger.debug(f"  📤 发送块[{chunk_count}]: {chunk[:1000]}..." if len(chunk) > 1000 else f"  📤 发送块[{chunk_count}]: {chunk}")
                    yield chunk
                logger.info(f"✅ 流式传输完成，共发送 {chunk_count} 个数据块")
            except Exception as e:
                logger.error(f"❌ 流式传输中断: {e}")
                raise
        
        return StreamingResponse(
            logged_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理请求时发生错误: {str(e)}")
        import traceback

        logger.error(f"错误堆栈: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
