#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import time
import uuid
import random
from datetime import datetime
from typing import Dict, List, Any, Optional, Generator, AsyncGenerator
import httpx
import asyncio

from app.core.config import settings
from app.utils.logger import get_logger
from app.utils.token_pool import get_token_pool, initialize_token_pool
from app.utils.user_agent import get_random_user_agent

logger = get_logger()


def get_zai_dynamic_headers(chat_id: str = "") -> Dict[str, str]:
    """
    生成 Z.AI 特定的动态浏览器 headers，包含随机 User-Agent
    使用通用的 UserAgent 工具，但添加 Z.AI 特定的业务逻辑

    Args:
        chat_id: 聊天 ID，用于生成正确的 Referer

    Returns:
        Dict[str, str]: 包含 Z.AI 特定配置的 headers
    """
    # 随机选择浏览器类型，偏向Chrome和Edge
    browser_choices = ["chrome", "chrome", "chrome", "edge", "edge", "firefox", "safari"]
    browser_type = random.choice(browser_choices)

    user_agent = get_random_user_agent(browser_type)

    # 提取版本信息
    chrome_version = "139"
    edge_version = "139"

    if "Chrome/" in user_agent:
        try:
            chrome_version = user_agent.split("Chrome/")[1].split(".")[0]
        except:
            pass

    if "Edg/" in user_agent:
        try:
            edge_version = user_agent.split("Edg/")[1].split(".")[0]
            sec_ch_ua = f'"Microsoft Edge";v="{edge_version}", "Chromium";v="{chrome_version}", "Not_A Brand";v="24"'
        except:
            sec_ch_ua = f'"Not_A Brand";v="8", "Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}"'
    elif "Firefox/" in user_agent:
        sec_ch_ua = None  # Firefox不使用sec-ch-ua
    else:
        sec_ch_ua = f'"Not_A Brand";v="8", "Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}"'

    # Z.AI 特定的 headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": user_agent,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "X-FE-Version": "prod-fe-1.0.79",
        "Origin": "https://chat.z.ai",
    }

    # 添加浏览器特定的 sec-ch-ua headers
    if sec_ch_ua:
        headers["sec-ch-ua"] = sec_ch_ua
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = '"Windows"'

    # 根据 chat_id 设置 Referer
    if chat_id:
        headers["Referer"] = f"https://chat.z.ai/c/{chat_id}"
    else:
        headers["Referer"] = "https://chat.z.ai/"

    return headers



def generate_uuid() -> str:
    """生成UUID v4"""
    return str(uuid.uuid4())


def get_auth_token_sync() -> str:
    """同步获取认证令牌（用于非异步场景）"""
    # 如果启用匿名模式，只尝试获取访客令牌
    if settings.ANONYMOUS_MODE:
        try:
            headers = get_zai_dynamic_headers()
            with httpx.Client() as client:
                response = client.get("https://chat.z.ai/api/v1/auths/", headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    token = data.get("token", "")
                    if token:
                        logger.debug(f"获取访客令牌成功: {token[:20]}...")
                        return token
        except Exception as e:
            logger.warning(f"获取访客令牌失败: {e}")

        # 匿名模式下，如果获取访客令牌失败，直接返回空
        logger.error("❌ 匿名模式下获取访客令牌失败")
        return ""

    # 非匿名模式：首先使用token池获取备份令牌
    token_pool = get_token_pool()
    if token_pool:
        token = token_pool.get_next_token()
        if token:
            logger.debug(f"从token池获取令牌: {token[:20]}...")
            return token

    # 如果没有备份token，尝试降级到匿名模式
    logger.warning("⚠️ 没有可用的备份token，尝试降级到匿名模式...")
    try:
        headers = get_zai_dynamic_headers()
        with httpx.Client() as client:
            response = client.get("https://chat.z.ai/api/v1/auths/", headers=headers, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                token = data.get("token", "")
                if token:
                    logger.info(f"✅ 降级到匿名模式成功: {token[:20]}...")
                    return token
    except Exception as e:
        logger.warning(f"降级到匿名模式失败: {e}")

    # 没有可用的token
    logger.error("❌ 所有认证方式都失败了")
    return ""


class ZAITransformer:
    """ZAI转换器类"""

    def __init__(self):
        """初始化转换器"""
        self.name = "zai"
        self.base_url = "https://chat.z.ai"
        self.api_url = settings.API_ENDPOINT
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

    async def get_token(self) -> str:
        """异步获取认证令牌"""
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
                            logger.debug(f"获取访客令牌成功: {token[:20]}...")
                            return token
            except Exception as e:
                logger.warning(f"异步获取访客令牌失败: {e}")

            # 匿名模式下，如果获取访客令牌失败，直接返回空
            logger.error("❌ 匿名模式下获取访客令牌失败")
            return ""

        # 非匿名模式：首先使用token池获取备份令牌
        token_pool = get_token_pool()
        if token_pool:
            token = token_pool.get_next_token()
            if token:
                logger.debug(f"从token池获取令牌: {token[:20]}...")
                return token

        # 如果没有备份token，尝试降级到匿名模式
        logger.warning("⚠️ 没有可用的备份token，尝试降级到匿名模式...")
        try:
            headers = get_zai_dynamic_headers()
            async with httpx.AsyncClient() as client:
                response = await client.get(self.auth_url, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    token = data.get("token", "")
                    if token:
                        logger.info(f"✅ 降级到匿名模式成功: {token[:20]}...")
                        return token
        except Exception as e:
            logger.warning(f"降级到匿名模式失败: {e}")

        # 没有可用的token
        logger.error("❌ 所有认证方式都失败了")
        return ""

    def mark_token_success(self, token: str):
        """标记token使用成功"""
        token_pool = get_token_pool()
        if token_pool:
            token_pool.mark_token_success(token)

    def mark_token_failure(self, token: str, error: Exception = None):
        """标记token使用失败"""
        token_pool = get_token_pool()
        if token_pool:
            token_pool.mark_token_failure(token, error)

    async def transform_request_in(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        转换OpenAI请求为z.ai格式
        整合现有功能：模型映射、MCP服务器等
        """
        logger.info(f"🔄 开始转换 OpenAI 请求到 Z.AI 格式: {request.get('model', settings.PRIMARY_MODEL)} -> Z.AI")

        # 获取认证令牌
        token = await self.get_token()
        logger.debug(f"  使用令牌: {token[:20] if token else 'None'}...")

        # 检查token是否有效
        if not token:
            # 提供详细的配置建议
            error_msg = "❌ 无法获取有效的认证令牌"
            suggestions = []

            if not settings.ANONYMOUS_MODE:
                suggestions.append("1. 设置 ANONYMOUS_MODE=true 启用匿名模式")

            if not settings.AUTH_TOKENS_FILE:
                suggestions.append("2. 配置 AUTH_TOKENS_FILE 并创建对应的token文件")
            elif settings.AUTH_TOKENS_FILE and not settings.auth_token_list:
                suggestions.append(f"3. 检查token文件 '{settings.AUTH_TOKENS_FILE}' 是否存在且包含有效token")

            if suggestions:
                error_msg += "\n建议的解决方案：\n" + "\n".join(suggestions)

            logger.error(error_msg)
            raise Exception("无法获取有效的认证令牌，请检查配置")

        # 确定请求的模型特性
        requested_model = request.get("model", settings.PRIMARY_MODEL)
        is_thinking = requested_model == settings.THINKING_MODEL or request.get("reasoning", False)
        is_search = requested_model == settings.SEARCH_MODEL
        is_air = requested_model == settings.AIR_MODEL

        # 获取上游模型ID（使用模型映射）
        upstream_model_id = self.model_mapping.get(requested_model, "0727-360B-API")
        logger.debug(f"  模型映射: {requested_model} -> {upstream_model_id}")

        # 处理消息列表
        logger.debug(f"  开始处理 {len(request.get('messages', []))} 条消息")
        messages = []
        for idx, orig_msg in enumerate(request.get("messages", [])):
            msg = orig_msg.copy()

            # 处理system角色转换
            if msg.get("role") == "system":

                msg["role"] = "user"
                content = msg.get("content")

                if isinstance(content, list):
                    msg["content"] = [
                        {"type": "text", "text": "This is a system command, you must enforce compliance."}
                    ] + content
                elif isinstance(content, str):
                    msg["content"] = f"This is a system command, you must enforce compliance.{content}"

            # 处理user角色的图片内容
            elif msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    new_content = []
                    for part_idx, part in enumerate(content):
                        # 处理图片URL（支持base64和http URL）
                        if (
                            part.get("type") == "image_url"
                            and part.get("image_url", {}).get("url")
                            and isinstance(part["image_url"]["url"], str)
                        ):
                            logger.debug(f"    消息[{idx}]内容[{part_idx}]: 检测到图片URL")
                            # 直接传递图片内容
                            new_content.append(part)
                        else:
                            new_content.append(part)
                    msg["content"] = new_content

            # 处理assistant消息中的reasoning_content
            elif msg.get("role") == "assistant" and msg.get("reasoning_content"):

                # 如果有reasoning_content，保留它
                pass

            messages.append(msg)

        # 构建MCP服务器列表
        mcp_servers = []
        if is_search:
            mcp_servers.append("deep-web-search")
            logger.info(f"🔍 检测到搜索模型，添加 deep-web-search MCP 服务器")
        else:
            logger.debug(f"  非搜索模型，不添加 MCP 服务器")

        logger.debug(f"  MCP服务器列表: {mcp_servers}")

        # 构建上游请求体
        chat_id = generate_uuid()

        body = {
            "stream": True,  # 总是使用流式
            "model": upstream_model_id,  # 使用映射后的模型ID
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
            "mcp_servers": mcp_servers,  # 保留MCP服务器支持
            "variables": {
                "{{USER_NAME}}": "Guest",
                "{{USER_LOCATION}}": "Unknown",
                "{{CURRENT_DATETIME}}": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "{{CURRENT_DATE}}": datetime.now().strftime("%Y-%m-%d"),
                "{{CURRENT_TIME}}": datetime.now().strftime("%H:%M:%S"),
                "{{CURRENT_WEEKDAY}}": datetime.now().strftime("%A"),
                "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",  # 使用更合适的时区
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
        if settings.TOOL_SUPPORT and not is_thinking and request.get("tools"):
            body["tools"] = request["tools"]
            logger.info(f"启用工具支持: {len(request['tools'])} 个工具")
        else:
            body["tools"] = None

        # 构建请求配置
        dynamic_headers = get_zai_dynamic_headers(chat_id)

        config = {
            "url": self.api_url,  # 使用原始URL
            "headers": {
                **dynamic_headers,  # 使用动态生成的headers
                "Authorization": f"Bearer {token}",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
        }

        logger.info("✅ 请求转换完成")

        # 记录关键的请求信息用于调试
        logger.debug(f"  📋 发送到Z.AI的关键信息:")
        logger.debug(f"    - 上游模型: {body['model']}")
        logger.debug(f"    - MCP服务器: {body['mcp_servers']}")
        logger.debug(f"    - web_search: {body['features']['web_search']}")
        logger.debug(f"    - auto_web_search: {body['features']['auto_web_search']}")
        logger.debug(f"    - 消息数量: {len(body['messages'])}")
        tools_count = len(body.get('tools') or [])
        logger.debug(f"    - 工具数量: {tools_count}")

        # 返回转换后的请求数据
        return {
            "body": body,
            "config": config,
            "token": token
        }

    async def transform_response_out(
        self, response_stream: Generator, context: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """
        转换z.ai响应为OpenAI格式
        支持流式和非流式输出
        """
        is_stream = context.get("req", {}).get("body", {}).get("stream", True)

        # 初始化结果对象（用于非流式）
        result = {
            "id": "",
            "choices": [
                {
                    "finish_reason": None,
                    "index": 0,
                    "message": {
                        "content": "",
                        "role": "assistant",
                    },
                }
            ],
            "created": int(time.time()),
            "model": context.get("req", {}).get("body", {}).get("model", ""),
            "object": "chat.completion",
            "usage": {
                "completion_tokens": 0,
                "prompt_tokens": 0,
                "total_tokens": 0,
            },
        }

        # 状态变量
        current_id = ""
        current_model = context.get("req", {}).get("body", {}).get("model", "")
        has_tool_call = False
        tool_args = ""
        tool_id = ""
        tool_call_usage = None
        content_index = 0
        has_thinking = False

        async for line in response_stream:
            if not line.strip():
                continue

            if line.startswith("data:"):
                chunk_str = line[5:].strip()
                if not chunk_str:
                    continue

                try:
                    chunk = json.loads(chunk_str)

                    if chunk.get("type") == "chat:completion":
                        data = chunk.get("data", {})

                        # 保存ID和模型信息
                        if data.get("id"):
                            current_id = data["id"]
                        if data.get("model"):
                            current_model = data["model"]

                        # 处理不同阶段
                        phase = data.get("phase")

                        if phase == "tool_call":
                            # 处理工具调用
                            if not has_tool_call:
                                has_tool_call = True

                                if is_stream:
                                    # 发送初始角色
                                    role_chunk = {
                                        "choices": [
                                            {
                                                "delta": {"role": "assistant"},
                                                "finish_reason": None,
                                                "index": 0,
                                            }
                                        ],
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
                                    yield f"data: {json.dumps(role_chunk)}\n\n"

                            # 处理工具调用块
                            tool_call_id = data.get("tool_call", {}).get("id", "")
                            tool_name = data.get("tool_call", {}).get("name", "")
                            delta_args = data.get("delta_tool_call", {}).get("arguments", "")

                            if tool_call_id and tool_call_id != tool_id:
                                # 新工具调用
                                if tool_id and is_stream:
                                    # 关闭前一个工具调用
                                    close_chunk = {
                                        "choices": [
                                            {
                                                "delta": {
                                                    "tool_calls": [
                                                        {"index": content_index, "function": {"arguments": ""}}
                                                    ]
                                                },
                                                "finish_reason": None,
                                                "index": 0,
                                            }
                                        ],
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
                                    yield f"data: {json.dumps(close_chunk)}\n\n"
                                    content_index += 1

                                tool_id = tool_call_id
                                tool_args = ""

                                if is_stream:
                                    # 发送新工具调用
                                    new_tool_chunk = {
                                        "choices": [
                                            {
                                                "delta": {
                                                    "tool_calls": [
                                                        {
                                                            "index": content_index,
                                                            "id": tool_call_id,
                                                            "type": "function",
                                                            "function": {"name": tool_name, "arguments": ""},
                                                        }
                                                    ]
                                                },
                                                "finish_reason": None,
                                                "index": 0,
                                            }
                                        ],
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
                                    yield f"data: {json.dumps(new_tool_chunk)}\n\n"

                            # 处理参数增量
                            if delta_args:
                                tool_args += delta_args
                                if is_stream:
                                    args_chunk = {
                                        "choices": [
                                            {
                                                "delta": {
                                                    "tool_calls": [
                                                        {
                                                            "index": content_index,
                                                            "function": {"arguments": delta_args},
                                                        }
                                                    ]
                                                },
                                                "finish_reason": None,
                                                "index": 0,
                                            }
                                        ],
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
                                    yield f"data: {json.dumps(args_chunk)}\n\n"

                        elif phase == "thinking":
                            # 处理思考内容
                            if not has_thinking:
                                has_thinking = True
                                # 初始化thinking字段
                                if not is_stream:
                                    result["choices"][0]["message"]["thinking"] = {"content": ""}

                                if is_stream:
                                    # 发送初始角色
                                    role_chunk = {
                                        "choices": [
                                            {
                                                "delta": {"role": "assistant"},
                                                "finish_reason": None,
                                                "index": 0,
                                            }
                                        ],
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
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

                                if is_stream:
                                    thinking_chunk = {
                                        "choices": [
                                            {
                                                "delta": {"thinking": {"content": content}},
                                                "finish_reason": None,
                                                "index": 0,
                                            }
                                        ],
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
                                    yield f"data: {json.dumps(thinking_chunk)}\n\n"
                                else:
                                    result["choices"][0]["message"]["thinking"]["content"] += content

                        elif phase == "answer":
                            # 处理答案内容
                            edit_content = data.get("edit_content", "")
                            delta_content = data.get("delta_content", "")

                            # 处理思考结束和答案开始
                            if edit_content and "</details>\n" in edit_content:
                                if has_thinking:
                                    signature = str(int(time.time() * 1000))

                                    if is_stream:
                                        # 发送思考签名
                                        sig_chunk = {
                                            "choices": [
                                                {
                                                    "delta": {
                                                        "role": "assistant",
                                                        "thinking": {"content": "", "signature": signature},
                                                    },
                                                    "finish_reason": None,
                                                    "index": 0,
                                                }
                                            ],
                                            "created": int(time.time()),
                                            "id": current_id,
                                            "model": current_model,
                                            "object": "chat.completion.chunk",
                                        }
                                        yield f"data: {json.dumps(sig_chunk)}\n\n"
                                        content_index += 1
                                    else:
                                        result["choices"][0]["message"]["thinking"]["signature"] = signature

                                # 提取答案内容
                                content_after = edit_content.split("</details>\n")[-1]
                                if content_after:
                                    if is_stream:
                                        content_chunk = {
                                            "choices": [
                                                {
                                                    "delta": {"role": "assistant", "content": content_after},
                                                    "finish_reason": None,
                                                    "index": 0,
                                                }
                                            ],
                                            "created": int(time.time()),
                                            "id": current_id,
                                            "model": current_model,
                                            "object": "chat.completion.chunk",
                                        }
                                        yield f"data: {json.dumps(content_chunk)}\n\n"
                                    else:
                                        result["choices"][0]["message"]["content"] += content_after

                            # 处理增量内容
                            elif delta_content:
                                if is_stream:
                                    # 如果还没有发送角色
                                    if not has_thinking and not has_tool_call:
                                        role_chunk = {
                                            "choices": [
                                                {
                                                    "delta": {"role": "assistant"},
                                                    "finish_reason": None,
                                                    "index": 0,
                                                }
                                            ],
                                            "created": int(time.time()),
                                            "id": current_id,
                                            "model": current_model,
                                            "object": "chat.completion.chunk",
                                        }
                                        yield f"data: {json.dumps(role_chunk)}\n\n"

                                    content_chunk = {
                                        "choices": [
                                            {
                                                "delta": {"role": "assistant", "content": delta_content},
                                                "finish_reason": None,
                                                "index": 0,
                                            }
                                        ],
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
                                    yield f"data: {json.dumps(content_chunk)}\n\n"
                                else:
                                    result["choices"][0]["message"]["content"] += delta_content

                            # 处理完成
                            if data.get("usage"):
                                usage = data["usage"]
                                if is_stream:
                                    finish_chunk = {
                                        "choices": [
                                            {
                                                "delta": {"role": "assistant", "content": ""},
                                                "finish_reason": "stop",
                                                "index": 0,
                                            }
                                        ],
                                        "usage": usage,
                                        "created": int(time.time()),
                                        "id": current_id,
                                        "model": current_model,
                                        "object": "chat.completion.chunk",
                                    }
                                    yield f"data: {json.dumps(finish_chunk)}\n\n"
                                    yield "data: [DONE]\n\n"
                                else:
                                    result["id"] = current_id
                                    result["model"] = current_model
                                    result["usage"] = usage
                                    result["choices"][0]["finish_reason"] = "stop"

                        elif phase == "other":
                            # 处理其他阶段（可能包含usage信息）
                            if data.get("usage"):
                                tool_call_usage = data["usage"]
                                if has_tool_call and is_stream:
                                    # 关闭最后一个工具调用并发送完成
                                    if tool_id:
                                        close_chunk = {
                                            "choices": [
                                                {
                                                    "delta": {
                                                        "tool_calls": [
                                                            {"index": content_index, "function": {"arguments": ""}}
                                                        ]
                                                    },
                                                    "finish_reason": "tool_calls",
                                                    "index": 0,
                                                }
                                            ],
                                            "usage": tool_call_usage,
                                            "created": int(time.time()),
                                            "id": current_id,
                                            "model": current_model,
                                            "object": "chat.completion.chunk",
                                        }
                                        yield f"data: {json.dumps(close_chunk)}\n\n"
                                        yield "data: [DONE]\n\n"

                except json.JSONDecodeError as e:
                    logger.debug(f"JSON解析错误: {e}")
                except Exception as e:
                    logger.error(f"处理chunk错误: {e}")

        # 非流式模式返回完整结果
        if not is_stream:
            yield json.dumps(result)
