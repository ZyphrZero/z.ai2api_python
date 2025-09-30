#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Z.AI 提供商适配器
"""

import json
import time
import uuid
import httpx
import asyncio
from datetime import datetime
from typing import Dict, List, Any, Optional, AsyncGenerator, Union

from app.providers.base import BaseProvider, ProviderConfig
from app.models.schemas import OpenAIRequest, Message
from app.core.config import settings
from app.utils.logger import get_logger
from app.utils.token_pool import get_token_pool
from app.core.zai_transformer import generate_uuid, get_zai_dynamic_headers
from app.utils.sse_tool_handler import SSEToolHandler

logger = get_logger()


class ZAIProvider(BaseProvider):
    """Z.AI 提供商"""
    
    def __init__(self):
        config = ProviderConfig(
            name="zai",
            api_endpoint=settings.API_ENDPOINT,
            timeout=30,
            headers=get_zai_dynamic_headers()
        )
        super().__init__(config)
        
        # Z.AI 特定配置
        self.base_url = "https://chat.z.ai"
        self.auth_url = f"{self.base_url}/api/v1/auths/"
        
        # 模型映射
        self.model_mapping = {
            # GLM-4.5 系列
            settings.PRIMARY_MODEL: "0727-360B-API",  # GLM-4.5
            settings.THINKING_MODEL: "0727-360B-API",  # GLM-4.5-Thinking
            settings.SEARCH_MODEL: "0727-360B-API",  # GLM-4.5-Search
            settings.AIR_MODEL: "0727-106B-API",  # GLM-4.5-Air
            # GLM-4.6 系列 (使用最新的 API ID)
            settings.GLM46_MODEL: "0929-360B-API",  # GLM-4.6
            settings.GLM46_THINKING_MODEL: "0929-360B-API",  # GLM-4.6-Thinking
        }
    
    def get_supported_models(self) -> List[str]:
        """获取支持的模型列表"""
        return [
            settings.PRIMARY_MODEL,
            settings.THINKING_MODEL,
            settings.SEARCH_MODEL,
            settings.AIR_MODEL,
            settings.GLM46_MODEL,
            settings.GLM46_THINKING_MODEL
        ]
    
    async def get_token(self) -> str:
        """获取认证令牌"""
        # 如果启用匿名模式，只尝试获取访客令牌
        if settings.ANONYMOUS_MODE:
            try:
                headers = get_zai_dynamic_headers()
                async with httpx.AsyncClient() as client:
                    response = await client.get(self.auth_url, headers=headers, timeout=10.0)
                    if response.status_code == 200:
                        data = response.json()
                        token = data.get("token", "")
                        if token:
                            self.logger.debug(f"获取访客令牌成功: {token[:20]}...")
                            return token
            except Exception as e:
                self.logger.warning(f"异步获取访客令牌失败: {e}")

            # 匿名模式下，如果获取访客令牌失败，直接返回空
            self.logger.error("❌ 匿名模式下获取访客令牌失败")
            return ""

        # 非匿名模式：首先使用token池获取备份令牌
        token_pool = get_token_pool()
        if token_pool:
            token = token_pool.get_next_token()
            if token:
                self.logger.debug(f"从token池获取令牌: {token[:20]}...")
                return token

        # 如果token池为空或没有可用token，使用配置的AUTH_TOKEN
        if settings.AUTH_TOKEN and settings.AUTH_TOKEN != "sk-your-api-key":
            self.logger.debug("使用配置的AUTH_TOKEN")
            return settings.AUTH_TOKEN

        self.logger.error("❌ 无法获取有效的认证令牌")
        return ""
    
    def mark_token_failure(self, token: str, error: Exception = None):
        """标记token使用失败"""
        token_pool = get_token_pool()
        if token_pool:
            token_pool.mark_token_failure(token, error)
    
    async def transform_request(self, request: OpenAIRequest) -> Dict[str, Any]:
        """转换OpenAI请求为Z.AI格式"""
        self.logger.info(f"🔄 转换 OpenAI 请求到 Z.AI 格式: {request.model}")
        
        # 获取认证令牌
        token = await self.get_token()
        
        # 处理消息格式
        messages = []
        for msg in request.messages:
            if isinstance(msg.content, str):
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
            elif isinstance(msg.content, list):
                # 处理多模态内容
                content_parts = []
                for part in msg.content:
                    if hasattr(part, 'type') and hasattr(part, 'text'):
                        content_parts.append({
                            "type": part.type,
                            "text": part.text
                        })
                messages.append({
                    "role": msg.role,
                    "content": content_parts
                })
        
        # 确定请求的模型特性
        requested_model = request.model
        is_thinking = requested_model == settings.THINKING_MODEL
        is_search = requested_model == settings.SEARCH_MODEL
        is_air = requested_model == settings.AIR_MODEL
        
        # 获取上游模型ID
        upstream_model_id = self.model_mapping.get(requested_model, "0727-360B-API")
        
        # 构建MCP服务器列表
        mcp_servers = []
        if is_search:
            mcp_servers.append("deep-web-search")
            self.logger.info("🔍 检测到搜索模型，添加 deep-web-search MCP 服务器")
        
        # 构建上游请求体
        chat_id = generate_uuid()
        
        body = {
            "stream": True,  # 总是使用流式
            "model": upstream_model_id,
            "messages": messages,
            "params": {},
            "features": {
                "image_generation": False,
                "web_search": is_search,
                "auto_web_search": is_search,
                "preview_mode": False,
                "flags": [],
                "features": [],
                "enable_thinking": is_thinking,
            },
            "background_tasks": {
                "title_generation": False,
                "tags_generation": False,
            },
            "mcp_servers": mcp_servers,
            "variables": {
                "{{USER_NAME}}": "Guest",
                "{{USER_LOCATION}}": "Unknown",
                "{{CURRENT_DATETIME}}": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "{{CURRENT_DATE}}": datetime.now().strftime("%Y-%m-%d"),
                "{{CURRENT_TIME}}": datetime.now().strftime("%H:%M:%S"),
                "{{CURRENT_WEEKDAY}}": datetime.now().strftime("%A"),
                "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
                "{{USER_LANGUAGE}}": "zh-CN",
            },
            "model_item": {
                "id": upstream_model_id,
                "name": requested_model,
                "owned_by": "z.ai"
            },
            "chat_id": chat_id,
            "id": generate_uuid(),
        }
        
        # 处理工具支持
        if settings.TOOL_SUPPORT and not is_thinking and request.tools:
            body["tools"] = request.tools
            self.logger.info(f"启用工具支持: {len(request.tools)} 个工具")
        else:
            body["tools"] = None
        
        # 处理其他参数
        if request.temperature is not None:
            body["params"]["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["params"]["max_tokens"] = request.max_tokens
        
        # 构建请求头
        headers = get_zai_dynamic_headers(chat_id)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        # 存储当前token用于错误处理
        self._current_token = token

        return {
            "url": self.config.api_endpoint,
            "headers": headers,
            "body": body,
            "token": token,
            "chat_id": chat_id,
            "model": requested_model
        }
    
    async def chat_completion(
        self,
        request: OpenAIRequest,
        **kwargs
    ) -> Union[Dict[str, Any], AsyncGenerator[str, None]]:
        """聊天完成接口"""
        self.log_request(request)

        try:
            # 转换请求
            transformed = await self.transform_request(request)

            # 根据请求类型返回响应
            if request.stream:
                # 流式响应
                return self._create_stream_response_with_retry(request, transformed)
            else:
                # 非流式响应
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        transformed["url"],
                        headers=transformed["headers"],
                        json=transformed["body"]
                    )

                    if not response.is_success:
                        error_msg = f"Z.AI API 错误: {response.status_code}"
                        self.log_response(False, error_msg)
                        return self.handle_error(Exception(error_msg))

                    return await self.transform_response(response, request, transformed)

        except Exception as e:
            self.log_response(False, str(e))
            return self.handle_error(e, "请求处理")

    async def _create_stream_response_with_retry(
        self,
        request: OpenAIRequest,
        transformed: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """创建带重试机制的流式响应生成器"""
        retry_count = 0
        last_error = None
        current_token = transformed.get("token", "")

        while retry_count <= settings.MAX_RETRIES:
            try:
                # 如果是重试，重新获取令牌并更新请求
                if retry_count > 0:
                    delay = settings.RETRY_DELAY
                    self.logger.warning(f"重试请求 ({retry_count}/{settings.MAX_RETRIES}) - 等待 {delay:.1f}s")
                    await asyncio.sleep(delay)

                    # 标记前一个token失败（如果不是匿名模式）
                    if current_token and not settings.ANONYMOUS_MODE:
                        self.mark_token_failure(current_token, Exception(f"Retry {retry_count}: {last_error}"))

                    # 重新获取令牌
                    self.logger.info("🔑 重新获取令牌用于重试...")
                    new_token = await self.get_token()
                    if not new_token:
                        self.logger.error("❌ 重试时无法获取有效的认证令牌")
                        raise Exception("重试时无法获取有效的认证令牌")
                    transformed["headers"]["Authorization"] = f"Bearer {new_token}"
                    current_token = new_token

                async with httpx.AsyncClient(timeout=60.0) as client:
                    # 发送请求到上游
                    self.logger.info(f"🎯 发送请求到 Z.AI: {transformed['url']}")
                    async with client.stream(
                        "POST",
                        transformed["url"],
                        json=transformed["body"],
                        headers=transformed["headers"],
                    ) as response:
                        # 检查响应状态码
                        if response.status_code == 400:
                            # 400 错误，触发重试
                            error_text = await response.aread()
                            error_msg = error_text.decode('utf-8', errors='ignore')
                            self.logger.warning(f"❌ 上游返回 400 错误 (尝试 {retry_count + 1}/{settings.MAX_RETRIES + 1})")

                            retry_count += 1
                            last_error = f"400 Bad Request: {error_msg}"

                            # 如果还有重试机会，继续循环
                            if retry_count <= settings.MAX_RETRIES:
                                continue
                            else:
                                # 达到最大重试次数，抛出错误
                                self.logger.error(f"❌ 达到最大重试次数 ({settings.MAX_RETRIES})，请求失败")
                                error_response = {
                                    "error": {
                                        "message": f"Request failed after {settings.MAX_RETRIES} retries: {last_error}",
                                        "type": "upstream_error",
                                        "code": 400
                                    }
                                }
                                yield f"data: {json.dumps(error_response)}\n\n"
                                yield "data: [DONE]\n\n"
                                return

                        elif response.status_code != 200:
                            # 其他错误，直接返回
                            self.logger.error(f"❌ 上游返回错误: {response.status_code}")
                            error_text = await response.aread()
                            error_msg = error_text.decode('utf-8', errors='ignore')
                            self.logger.error(f"❌ 错误详情: {error_msg}")

                            error_response = {
                                "error": {
                                    "message": f"Upstream error: {response.status_code}",
                                    "type": "upstream_error",
                                    "code": response.status_code
                                }
                            }
                            yield f"data: {json.dumps(error_response)}\n\n"
                            yield "data: [DONE]\n\n"
                            return

                        # 200 成功，处理响应
                        if retry_count > 0:
                            self.logger.info(f"✨ 第 {retry_count} 次重试成功")

                        # 标记token使用成功（如果不是匿名模式）
                        if current_token and not settings.ANONYMOUS_MODE:
                            token_pool = get_token_pool()
                            if token_pool:
                                token_pool.mark_token_success(current_token)

                        # 处理流式响应
                        chat_id = transformed["chat_id"]
                        model = transformed["model"]
                        async for chunk in self._handle_stream_response(response, chat_id, model, request, transformed):
                            yield chunk
                        return

            except Exception as e:
                self.logger.error(f"❌ 流处理错误: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

                # 标记token失败（如果不是匿名模式）
                if current_token and not settings.ANONYMOUS_MODE:
                    self.mark_token_failure(current_token, e)

                # 检查是否还可以重试
                retry_count += 1
                last_error = str(e)

                if retry_count > settings.MAX_RETRIES:
                    # 达到最大重试次数，返回错误
                    self.logger.error(f"❌ 达到最大重试次数 ({settings.MAX_RETRIES})，流处理失败")
                    error_response = {
                        "error": {
                            "message": f"Stream processing failed after {settings.MAX_RETRIES} retries: {last_error}",
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
        """转换Z.AI响应为OpenAI格式"""
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
        """处理Z.AI流式响应"""
        self.logger.info(f"✅ Z.AI 响应成功，开始处理 SSE 流")

        # 初始化工具处理器（如果需要）
        has_tools = transformed["body"].get("tools") is not None
        tool_handler = None

        if has_tools:
            tool_handler = SSEToolHandler(model, stream=True)
            self.logger.info(f"🔧 初始化工具处理器: {len(transformed['body'].get('tools', []))} 个工具")

        # 处理状态
        has_thinking = False
        thinking_signature = None

        # 处理SSE流
        buffer = ""
        line_count = 0
        self.logger.debug("📡 开始接收 SSE 流数据...")

        try:
            async for line in response.aiter_lines():
                line_count += 1
                if not line:
                    continue

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
                                yield "data: [DONE]\n\n"
                            continue

                        self.logger.debug(f"📦 解析数据块: {chunk_str[:1000]}..." if len(chunk_str) > 1000 else f"📦 解析数据块: {chunk_str}")

                        try:
                            chunk = json.loads(chunk_str)

                            if chunk.get("type") == "chat:completion":
                                data = chunk.get("data", {})
                                phase = data.get("phase")

                                # 记录每个阶段（只在阶段变化时记录）
                                if phase and phase != getattr(self, '_last_phase', None):
                                    self.logger.info(f"📈 SSE 阶段: {phase}")
                                    self._last_phase = phase

                                # 使用工具处理器处理所有阶段
                                if tool_handler:
                                    # 构建 SSE 数据块，包含所有必要字段
                                    sse_chunk = {
                                        "phase": phase,
                                        "edit_content": data.get("edit_content", ""),
                                        "delta_content": data.get("delta_content", ""),
                                        "edit_index": data.get("edit_index"),
                                        "usage": data.get("usage", {})
                                    }

                                    # 处理工具调用并输出结果
                                    for output in tool_handler.process_sse_chunk(sse_chunk):
                                        yield output

                                # 非工具调用模式 - 处理思考内容
                                elif phase == "thinking":
                                    if not has_thinking:
                                        has_thinking = True
                                        # 发送初始角色
                                        role_chunk = self.create_openai_chunk(
                                            chat_id,
                                            model,
                                            {"role": "assistant"}
                                        )
                                        yield await self.format_sse_chunk(role_chunk)

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

                                        thinking_chunk = self.create_openai_chunk(
                                            chat_id,
                                            model,
                                            {
                                                "role": "assistant",
                                                "thinking": {"content": content}
                                            }
                                        )
                                        yield await self.format_sse_chunk(thinking_chunk)

                                # 处理答案内容
                                elif phase == "answer":
                                    edit_content = data.get("edit_content", "")
                                    delta_content = data.get("delta_content", "")

                                    # 处理思考结束和答案开始
                                    if edit_content and "</details>\n" in edit_content:
                                        if has_thinking:
                                            # 发送思考签名
                                            thinking_signature = str(int(time.time() * 1000))
                                            sig_chunk = self.create_openai_chunk(
                                                chat_id,
                                                model,
                                                {
                                                    "role": "assistant",
                                                    "thinking": {
                                                        "content": "",
                                                        "signature": thinking_signature,
                                                    }
                                                }
                                            )
                                            yield await self.format_sse_chunk(sig_chunk)

                                        # 提取答案内容
                                        content_after = edit_content.split("</details>\n")[-1]
                                        if content_after:
                                            content_chunk = self.create_openai_chunk(
                                                chat_id,
                                                model,
                                                {
                                                    "role": "assistant",
                                                    "content": content_after
                                                }
                                            )
                                            yield await self.format_sse_chunk(content_chunk)

                                    # 处理增量内容
                                    elif delta_content:
                                        # 如果还没有发送角色
                                        if not has_thinking:
                                            role_chunk = self.create_openai_chunk(
                                                chat_id,
                                                model,
                                                {"role": "assistant"}
                                            )
                                            yield await self.format_sse_chunk(role_chunk)

                                        content_chunk = self.create_openai_chunk(
                                            chat_id,
                                            model,
                                            {
                                                "role": "assistant",
                                                "content": delta_content
                                            }
                                        )
                                        output_data = await self.format_sse_chunk(content_chunk)
                                        self.logger.debug(f"➡️ 输出内容块到客户端: {output_data}")
                                        yield output_data

                                    # 处理完成
                                    if data.get("usage"):
                                        self.logger.info(f"📦 完成响应 - 使用统计: {json.dumps(data['usage'])}")

                                        # 只有在非工具调用模式下才发送普通完成信号
                                        if not tool_handler:
                                            finish_chunk = self.create_openai_chunk(
                                                chat_id,
                                                model,
                                                {"role": "assistant", "content": ""},
                                                "stop"
                                            )
                                            finish_chunk["usage"] = data["usage"]

                                            finish_output = await self.format_sse_chunk(finish_chunk)
                                            self.logger.debug(f"➡️ 发送完成信号: {finish_output[:1000]}...")
                                            yield finish_output
                                            self.logger.debug("➡️ 发送 [DONE]")
                                            yield "data: [DONE]\n\n"

                        except json.JSONDecodeError as e:
                            self.logger.debug(f"❌ JSON解析错误: {e}, 内容: {chunk_str[:1000]}")
                        except Exception as e:
                            self.logger.error(f"❌ 处理chunk错误: {e}")

            # 工具处理器会自动发送结束信号，这里不需要重复发送
            if not tool_handler:
                self.logger.debug("📤 发送最终 [DONE] 信号")
                yield "data: [DONE]\n\n"

            self.logger.info(f"✅ SSE 流处理完成，共处理 {line_count} 行数据")

        except Exception as e:
            self.logger.error(f"❌ 流式响应处理错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # 发送错误结束块
            yield await self.format_sse_chunk(
                self.create_openai_chunk(chat_id, model, {}, "stop")
            )
            yield "data: [DONE]\n\n"
    
    async def _handle_non_stream_response(
        self, 
        response: httpx.Response, 
        chat_id: str, 
        model: str
    ) -> Dict[str, Any]:
        """处理非流式响应"""
        # 简化的非流式响应处理
        content = "非流式响应处理中..."
        return self.create_openai_response(chat_id, model, content)
