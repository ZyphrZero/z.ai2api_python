#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Z.AI 提供商适配器
"""

import json
import time
import uuid
import httpx
import hmac
import hashlib
import base64
import asyncio
from urllib.parse import urlencode
import os
import uuid
import random
from datetime import datetime
from typing import Dict, List, Any, Optional, AsyncGenerator, Union
from app.utils.user_agent import get_random_user_agent
from app.utils.fe_version import get_latest_fe_version
from app.utils.signature import generate_signature
from app.providers.base import BaseProvider, ProviderConfig
from app.models.schemas import OpenAIRequest, Message
from app.core.config import settings
from app.utils.logger import get_logger
from app.utils.token_pool import get_token_pool
from app.utils.tool_call_handler import (
    process_messages_with_tools,
    parse_and_extract_tool_calls,
)

logger = get_logger()

def generate_uuid() -> str:
    """生成UUID v4"""
    return str(uuid.uuid4())

def get_zai_dynamic_headers(chat_id: str = "") -> Dict[str, str]:
    """生成 Z.AI 特定的动态浏览器 headers"""
    browser_choices = ["chrome", "chrome", "chrome", "edge", "edge", "firefox", "safari"]
    browser_type = random.choice(browser_choices)
    user_agent = get_random_user_agent(browser_type)
    fe_version = get_latest_fe_version()

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
        sec_ch_ua = None
    else:
        sec_ch_ua = f'"Not_A Brand";v="8", "Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}"'

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
            settings.GLM45_MODEL: "0727-360B-API",  # GLM-4.5
            settings.GLM45_THINKING_MODEL: "0727-360B-API",  # GLM-4.5-Thinking
            settings.GLM45_SEARCH_MODEL: "0727-360B-API",  # GLM-4.5-Search
            settings.GLM45_AIR_MODEL: "0727-106B-API",  # GLM-4.5-Air
            settings.GLM46V_MODEL: "glm-4.6v",  # GLM-4.6V多模态
            settings.GLM47_MODEL: "glm-4.7",  # GLM-4.7
            settings.GLM47_THINKING_MODEL: "glm-4.7",  # GLM-4.7-Thinking
            settings.GLM47_SEARCH_MODEL: "glm-4.7",  # GLM-4.7-Search
            settings.GLM47_ADVANCED_SEARCH_MODEL: "glm-4.7",  # GLM-4.7-advanced-search
        }
    
    def get_supported_models(self) -> List[str]:
        """获取支持的模型列表"""
        return [
            settings.GLM45_MODEL,
            settings.GLM45_THINKING_MODEL,
            settings.GLM45_SEARCH_MODEL,
            settings.GLM45_AIR_MODEL,
            settings.GLM46V_MODEL,
            settings.GLM47_MODEL,
            settings.GLM47_THINKING_MODEL,
            settings.GLM47_SEARCH_MODEL,
            settings.GLM47_ADVANCED_SEARCH_MODEL,
        ]

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

    async def get_token(self) -> str:
        """获取认证令牌"""
        # 如果启用匿名模式，只尝试获取访客令牌
        if settings.ANONYMOUS_MODE:
            max_retries = 3
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    headers = get_zai_dynamic_headers()
                    self.logger.debug(f"尝试获取访客令牌 (第{retry_count + 1}次): {self.auth_url}")
                    self.logger.debug(f"请求头: {headers}")

                    # Get proxy configuration
                    proxies = self._get_proxy_config()

                    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, proxy=proxies) as client:
                        response = await client.get(self.auth_url, headers=headers)
                        
                        self.logger.debug(f"响应状态码: {response.status_code}")
                        self.logger.debug(f"响应头: {dict(response.headers)}")
                        
                        if response.status_code == 200:
                            data = response.json()
                            self.logger.debug(f"响应数据: {data}")
                            
                            token = data.get("token", "")
                            if token:
                                # 判断令牌类型（通过检查邮箱或user_id）
                                email = data.get("email", "")
                                is_guest = "@guest.com" in email or "Guest-" in email
                                token_type = "匿名用户" if is_guest else "认证用户"
                                self.logger.info(f"✅ 获取令牌成功 ({token_type}): {token[:20]}...")
                                return token
                            else:
                                self.logger.warning(f"响应中未找到token字段: {data}")
                        elif response.status_code == 405:
                            # WAF拦截
                            self.logger.error(f"🚫 请求被WAF拦截 (状态码405),请求头可能被识别为异常,请稍后重试...")
                            break
                        else:
                            self.logger.warning(f"HTTP请求失败,状态码: {response.status_code}")
                            try:
                                error_data = response.json()
                                self.logger.warning(f"错误响应: {error_data}")
                            except:
                                self.logger.warning(f"错误响应文本: {response.text}")
                                
                except httpx.TimeoutException as e:
                    self.logger.warning(f"请求超时 (第{retry_count + 1}次): {e}")
                except httpx.ConnectError as e:
                    self.logger.warning(f"连接错误 (第{retry_count + 1}次): {e}")
                except httpx.HTTPStatusError as e:
                    self.logger.warning(f"HTTP状态错误 (第{retry_count + 1}次): {e}")
                except json.JSONDecodeError as e:
                    self.logger.warning(f"JSON解析错误 (第{retry_count + 1}次): {e}")
                except Exception as e:
                    self.logger.warning(f"异步获取访客令牌失败 (第{retry_count + 1}次): {e}")
                    import traceback
                    self.logger.debug(f"错误堆栈: {traceback.format_exc()}")
                
                retry_count += 1
                if retry_count < max_retries:
                    self.logger.info(f"等待2秒后重试...")
                    await asyncio.sleep(2)

            # 匿名模式下，如果获取访客令牌失败，直接返回空
            self.logger.error("❌ 匿名模式下获取访客令牌失败，已重试3次")
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
            self.logger.debug(f"使用配置的AUTH_TOKEN")
            return settings.AUTH_TOKEN

        self.logger.error("❌ 无法获取有效的认证令牌")
        return ""
    
    def mark_token_failure(self, token: str, error: Exception = None):
        """标记token使用失败"""
        token_pool = get_token_pool()
        if token_pool:
            token_pool.mark_token_failure(token, error)

    async def upload_image(self, data_url: str, chat_id: str, token: str, user_id: str) -> Optional[Dict]:
        """上传 base64 编码的图片到 Z.AI 服务器

        Args:
            data_url: data:image/xxx;base64,... 格式的图片数据
            chat_id: 当前对话ID
            token: 认证令牌
            user_id: 用户ID

        Returns:
            上传成功返回完整的文件信息字典，失败返回None
        """
        if settings.ANONYMOUS_MODE or not data_url.startswith("data:"):
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
                "Referer": f"{self.base_url}/c/{chat_id}",
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
            async with httpx.AsyncClient(timeout=30.0, proxy=proxies) as client:
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

                    # 返回符合 Z.AI 格式的文件信息
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

    async def transform_request(self, request: OpenAIRequest) -> Dict[str, Any]:
        """转换OpenAI请求为Z.AI格式"""
        self.logger.info(f"🔄 转换 OpenAI 请求到 Z.AI 格式: {request.model}")

        # 获取认证令牌
        token = await self.get_token()
        user_id = _extract_user_id_from_token(token)

        # 生成 chat_id（用于图片上传）
        chat_id = generate_uuid()

        # 处理消息格式 - Z.AI 使用单独的 files 字段传递图片
        messages = []
        files = []  # 存储上传的图片文件信息

        for msg in request.messages:
            if isinstance(msg.content, str):
                # 纯文本消息
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
            elif isinstance(msg.content, list):
                # 多模态内容：分离文本和图片
                text_parts = []
                image_parts = []  # 存储图片引用

                for part in msg.content:
                    if hasattr(part, 'type'):
                        if part.type == 'text' and hasattr(part, 'text'):
                            # 文本部分
                            text_parts.append(part.text or '')
                        elif part.type == 'image_url' and hasattr(part, 'image_url'):
                            # 图片部分 - 提取并上传
                            image_url = None
                            if hasattr(part.image_url, 'url'):
                                image_url = part.image_url.url
                            elif isinstance(part.image_url, dict) and 'url' in part.image_url:
                                image_url = part.image_url['url']

                            if image_url:
                                self.logger.debug(f"✅ 检测到图片: {image_url[:50]}...")

                                # 如果是 base64 编码的图片，上传并添加到 files 数组
                                if image_url.startswith("data:") and not settings.ANONYMOUS_MODE:
                                    self.logger.info(f"🔄 上传 base64 图片到 Z.AI 服务器")
                                    file_info = await self.upload_image(image_url, chat_id, token, user_id)

                                    if file_info:
                                        files.append(file_info)
                                        self.logger.info(f"✅ 图片已添加到 files 数组")

                                        # 在消息中保留图片引用
                                        image_ref = f"{file_info['id']}_{file_info['name']}"
                                        image_parts.append({
                                            "type": "image_url",
                                            "image_url": {
                                                "url": image_ref
                                            }
                                        })
                                        self.logger.debug(f"📎 图片引用: {image_ref}")
                                    else:
                                        # 上传失败，添加错误提示
                                        self.logger.warning(f"⚠️ 图片上传失败")
                                        text_parts.append("[系统提示: 图片上传失败]")
                                else:
                                    # 非 base64 图片或匿名模式，直接使用原URL
                                    if not settings.ANONYMOUS_MODE:
                                        self.logger.warning(f"⚠️ 非 base64 图片或匿名模式，保留原始URL")
                                    image_parts.append({
                                        "type": "image_url",
                                        "image_url": {"url": image_url}
                                    })
                    elif isinstance(part, dict):
                        # 直接是字典格式的内容
                        if part.get('type') == 'text':
                            text_parts.append(part.get('text', ''))
                        elif part.get('type') == 'image_url':
                            image_url = part.get('image_url', {}).get('url', '')
                            if image_url:
                                self.logger.debug(f"✅ 检测到图片: {image_url[:50]}...")

                                # 如果是 base64 编码的图片，上传并添加到 files 数组
                                if image_url.startswith("data:") and not settings.ANONYMOUS_MODE:
                                    self.logger.info(f"🔄 上传 base64 图片到 Z.AI 服务器")
                                    file_info = await self.upload_image(image_url, chat_id, token, user_id)

                                    if file_info:
                                        files.append(file_info)
                                        self.logger.info(f"✅ 图片已添加到 files 数组")

                                        # 在消息中保留图片引用
                                        image_ref = f"{file_info['id']}_{file_info['name']}"
                                        image_parts.append({
                                            "type": "image_url",
                                            "image_url": {
                                                "url": image_ref
                                            }
                                        })
                                        self.logger.debug(f"📎 图片引用: {image_ref}")
                                    else:
                                        # 上传失败，添加错误提示
                                        self.logger.warning(f"⚠️ 图片上传失败")
                                        text_parts.append("[系统提示: 图片上传失败]")
                                else:
                                    # 非 base64 图片或匿名模式
                                    if not settings.ANONYMOUS_MODE:
                                        self.logger.warning(f"⚠️ 非 base64 图片或匿名模式，保留原始URL")
                                    image_parts.append({
                                        "type": "image_url",
                                        "image_url": {"url": image_url}
                                    })
                    elif isinstance(part, str):
                        # 纯字符串部分
                        text_parts.append(part)

                # 构建多模态消息内容
                message_content = []

                # 添加文本部分
                combined_text = " ".join(text_parts).strip()
                if combined_text:
                    message_content.append({
                        "type": "text",
                        "text": combined_text
                    })

                # 添加图片部分（保持图片引用在消息中）
                message_content.extend(image_parts)

                # 只有在有内容时才添加消息
                if message_content:
                    messages.append({
                        "role": msg.role,
                        "content": message_content  # ✅ 多模态内容数组
                    })
        
        # 确定请求的模型特性
        # Extract last user message text for signing (提取最后一条用户消息的文本用于签名)
        last_user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    # 纯文本消息
                    last_user_text = content
                    break
                elif isinstance(content, list):
                    # 多模态消息：只提取文本部分用于签名
                    texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    last_user_text = " ".join([t for t in texts if t]).strip()
                    break
        requested_model = request.model
        is_thinking = "-thinking" in requested_model.casefold()
        is_search = "-search" in requested_model.casefold()
        is_advanced_search = requested_model == settings.GLM47_ADVANCED_SEARCH_MODEL
        is_air = "-air" in requested_model.casefold()

        # 获取上游模型ID
        upstream_model_id = self.model_mapping.get(requested_model, "0727-360B-API")

        # ⚠️ 重要：在构建 body 之前处理工具调用！
        # 处理工具支持 - 使用提示词注入方式
        if settings.TOOL_SUPPORT and not is_thinking and request.tools:
            tool_choice = getattr(request, 'tool_choice', 'auto') or 'auto'
            messages = process_messages_with_tools(
                messages=messages,
                tools=request.tools,
                tool_choice=tool_choice
            )
            self.logger.info(f"🔧 工具调用已通过提示词注入: {len(request.tools)} 个工具")

        # 构建MCP服务器列表
        mcp_servers = []
        if is_advanced_search:
            mcp_servers.append("advanced-search")
            self.logger.info("🔍 检测到高级搜索模型，添加 advanced-search MCP 服务器")

        # 构建上游请求体
        body = {
            "stream": True,  # 总是使用流式
            "model": upstream_model_id,
            "messages": messages,  # ✅ messages 已经包含工具提示词
            "signature_prompt": last_user_text,  # 用于签名的最后一条用户消息
            "files": files,  # 图片文件数组
            "params": {},
            "features": {
                "image_generation": False,
                "web_search": is_search or is_advanced_search,
                "auto_web_search": is_search or is_advanced_search,
                "preview_mode": is_search or is_advanced_search,
                "flags": [],
                "features": [
                    {
                        "type": "mcp",
                        "server": "vibe-coding",
                        "status": "hidden"
                    },
                    {
                        "type": "mcp",
                        "server": "ppt-maker",
                        "status": "hidden"
                    },
                    {
                        "type": "mcp",
                        "server": "image-search",
                        "status": "hidden"
                    },
                    {
                        "type": "mcp",
                        "server": "deep-research",
                        "status": "hidden"
                    },
                    {
                        "type": "tool_selector",
                        "server": "tool_selector",
                        "status": "hidden"
                    },
                    {
                        "type": "mcp",
                        "server": "advanced-search",
                        "status": "hidden"
                    }
                ],
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

        # 不传递 tools 给上游,使用提示工程方式
        body["tools"] = None
        
        # 处理其他参数
        if request.temperature is not None:
            body["params"]["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["params"]["max_tokens"] = request.max_tokens
        
        # Dual-layer HMAC signing metadata and header
        user_id = _extract_user_id_from_token(token)
        timestamp_ms = int(time.time() * 1000)
        request_id = generate_uuid()
        fe_version = get_latest_fe_version()
        try:
            signing_metadata = f"requestId,{request_id},timestamp,{timestamp_ms},user_id,{user_id}"
            prompt_for_signature = last_user_text or ""
            signature_result = generate_signature(
                e=signing_metadata,
                t=prompt_for_signature,
                s=timestamp_ms,
            )
            signature = signature_result["signature"]
            logger.debug(f"[Z.AI] 生成签名成功: {signature[:16]}... (user_id={user_id}, request_id={request_id})")
        except Exception as e:
            logger.error(f"[Z.AI] 签名生成失败: {e}")
            signature = ""

        # 构建请求头
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-FE-Version": fe_version,
            "X-Signature": signature,
        }

        query_params = {
            "timestamp": str(timestamp_ms),
            "requestId": request_id,
            "user_id": user_id,
            "token": token,
            "version": "0.0.1",
            "platform": "web",
            "current_url": f"https://chat.z.ai/c/{chat_id}",
            "pathname": f"/c/{chat_id}",
            "signature_timestamp": str(timestamp_ms),
        }
        signed_url = f"{self.config.api_endpoint}?{urlencode(query_params)}"

        # 记录请求详情用于调试
        logger.debug(f"[Z.AI] 请求头: Authorization=Bearer *****, X-Signature={signature[:16] if signature else '(空)'}...")
        logger.debug(f"[Z.AI] URL 参数: timestamp={timestamp_ms}, requestId={request_id}, user_id={user_id}")
        
        # 存储当前token用于错误处理
        self._current_token = token

        return {
            "url": signed_url,
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
                return self._create_stream_response(request, transformed)
            else:
                # Get proxy configuration
                proxies = self._get_proxy_config()

                # 非流式响应
                async with httpx.AsyncClient(timeout=30.0, proxy=proxies) as client:
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

    
    async def _create_stream_response(
        self,
        request: OpenAIRequest,
        transformed: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:

        current_token = transformed.get("token", "")
        try:
            # Get proxy configuration
            proxies = self._get_proxy_config()

            async with httpx.AsyncClient(
                timeout=60.0,
                http2=True,
                proxy=proxies,
            ) as client:
                self.logger.info(f"🎯 发送请求到 Z.AI: {transformed['url']}")
                # self.logger.info(f"📦 请求体 model: {transformed['body']['model']}")
                # self.logger.info(f"📦 请求体 messages: {json.dumps(transformed['body']['messages'], ensure_ascii=False)}")
                async with client.stream(
                    "POST",
                    transformed["url"],
                    json=transformed["body"],
                    headers=transformed["headers"],
                ) as response:
                    if response.status_code != 200:
                        self.logger.error(f"❌ 上游返回错误: {response.status_code}")
                        error_text = await response.aread()
                        error_msg = error_text.decode('utf-8', errors='ignore')
                        if error_msg:
                            self.logger.error(f"❌ 错误详情: {error_msg}")

                        # 特殊处理 405 状态码(WAF拦截)
                        if response.status_code == 405:
                            self.logger.error(f"🚫 请求被上游WAF拦截,可能是请求头或签名异常,请稍后重试...")
                            error_response = {
                                "error": {
                                    "message": "请求被上游WAF拦截(405 Method Not Allowed),可能是请求头或签名异常,请稍后重试...",
                                    "type": "waf_blocked",
                                    "code": 405
                                }
                            }
                        else:
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

                    if current_token and not settings.ANONYMOUS_MODE:
                        token_pool = get_token_pool()
                        if token_pool:
                            token_pool.mark_token_success(current_token)

                    chat_id = transformed["chat_id"]
                    model = transformed["model"]
                    async for chunk in self._handle_stream_response(response, chat_id, model, request, transformed):
                        yield chunk
                    return
        except Exception as e:
            self.logger.error(f"❌ 流处理错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            if current_token and not settings.ANONYMOUS_MODE:
                self.mark_token_failure(current_token, e)
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

        # 检查是否启用了工具调用 (通过检查原始请求)
        has_tools = settings.TOOL_SUPPORT and request.tools is not None and len(request.tools) > 0

        # 累积内容缓冲区,用于提取工具调用
        buffered_content = ""
        has_sent_role = False

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

                                # 处理思考内容
                                if phase == "thinking":
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
                                                "reasoning_content": content
                                            }
                                        )
                                        yield await self.format_sse_chunk(thinking_chunk)

                                # 处理答案内容
                                elif phase == "answer":
                                    delta_content = data.get("delta_content", "")
                                    edit_content = data.get("edit_content", "")

                                    # 累积内容(用于工具调用提取)
                                    if delta_content:
                                        buffered_content += delta_content
                                    elif edit_content:
                                        buffered_content = edit_content

                                    # 如果包含 usage,说明流式结束
                                    if data.get("usage"):
                                        usage = data["usage"]
                                        self.logger.info(f"📦 完成响应 - 使用统计: {json.dumps(usage)}")

                                        # 尝试从缓冲区提取 tool_calls
                                        tool_calls = None

                                        if has_tools:
                                            tool_calls, _ = parse_and_extract_tool_calls(buffered_content)

                                        if tool_calls:
                                            # 发现工具调用
                                            self.logger.info(f"🔧 从响应中提取到 {len(tool_calls)} 个工具调用")

                                            if not has_sent_role:
                                                role_chunk = self.create_openai_chunk(
                                                    chat_id,
                                                    model,
                                                    {"role": "assistant"}
                                                )
                                                yield await self.format_sse_chunk(role_chunk)
                                                has_sent_role = True

                                            # 发送工具调用
                                            for idx, tc in enumerate(tool_calls):
                                                tool_chunk = self.create_openai_chunk(
                                                    chat_id,
                                                    model,
                                                    {
                                                        "role": "assistant",
                                                        "tool_calls": [{
                                                            "index": idx,
                                                            "id": tc.get("id", f"call_{idx}"),
                                                            "type": "function",
                                                            "function": {
                                                                "name": tc.get("function", {}).get("name", ""),
                                                                "arguments": tc.get("function", {}).get("arguments", "")
                                                            }
                                                        }]
                                                    }
                                                )
                                                yield await self.format_sse_chunk(tool_chunk)

                                            # 发送完成块
                                            finish_chunk = self.create_openai_chunk(
                                                chat_id,
                                                model,
                                                {"role": "assistant"},
                                                "tool_calls"
                                            )
                                            finish_chunk["usage"] = usage
                                            yield await self.format_sse_chunk(finish_chunk)
                                            yield "data: [DONE]\n\n"

                                        else:
                                            # 没有工具调用,流式内容已经在上面的增量输出中发送过了
                                            # 这里只需要发送 finish 块即可,不要再次发送内容
                                            if not has_sent_role and not has_thinking:
                                                role_chunk = self.create_openai_chunk(
                                                    chat_id,
                                                    model,
                                                    {"role": "assistant"}
                                                )
                                                yield await self.format_sse_chunk(role_chunk)
                                                has_sent_role = True

                                            finish_chunk = self.create_openai_chunk(
                                                chat_id,
                                                model,
                                                {"role": "assistant", "content": ""},
                                                "stop"
                                            )
                                            finish_chunk["usage"] = usage
                                            yield await self.format_sse_chunk(finish_chunk)
                                            yield "data: [DONE]\n\n"
                                    else:
                                        # 流式过程中,输出答案内容（即使有工具调用也要显示）
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
                                            if not has_sent_role and not has_thinking:
                                                role_chunk = self.create_openai_chunk(
                                                    chat_id,
                                                    model,
                                                    {"role": "assistant"}
                                                )
                                                yield await self.format_sse_chunk(role_chunk)
                                                has_sent_role = True

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

                        except json.JSONDecodeError as e:
                            self.logger.debug(f"❌ JSON解析错误: {e}, 内容: {chunk_str[:1000]}")
                        except Exception as e:
                            self.logger.error(f"❌ 处理chunk错误: {e}")

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
        """处理非流式响应

        说明：上游始终以 SSE 形式返回（transform_request 固定 stream=True），
        因此这里需要聚合 aiter_lines() 的 data: 块，提取 usage、思考内容与答案内容，
        并最终产出一次性 OpenAI 格式响应。
        """
        final_content = ""
        reasoning_content = ""
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

                # 仅处理以 data: 开头的 SSE 行，其余行尝试作为错误/JSON 忽略
                if not line.startswith("data:"):
                    # 尝试解析为错误 JSON
                    try:
                        maybe_err = json.loads(line)
                        if isinstance(maybe_err, dict) and (
                            "error" in maybe_err or "code" in maybe_err or "message" in maybe_err
                        ):
                            # 统一错误处理
                            msg = (
                                (maybe_err.get("error") or {}).get("message")
                                if isinstance(maybe_err.get("error"), dict)
                                else maybe_err.get("message")
                            ) or "上游返回错误"
                            return self.handle_error(Exception(msg), "API响应")
                    except Exception:
                        pass
                    continue

                data_str = line[5:].strip()
                if not data_str or data_str in ("[DONE]", "DONE", "done"):
                    continue

                # 解析 SSE 数据块
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if chunk.get("type") != "chat:completion":
                    continue

                data = chunk.get("data", {})
                phase = data.get("phase")
                delta_content = data.get("delta_content", "")
                edit_content = data.get("edit_content", "")

                # 记录用量（通常在最后块中出现，但这里每次覆盖保持最新）
                if data.get("usage"):
                    try:
                        usage_info = data["usage"]
                    except Exception:
                        pass

                # 思考阶段聚合（去除 <details><summary>... 包裹头）
                if phase == "thinking":
                    if delta_content:
                        if delta_content.startswith("<details"):
                            cleaned = (
                                delta_content.split("</summary>\n>")[-1].strip()
                                if "</summary>\n>" in delta_content
                                else delta_content
                            )
                        else:
                            cleaned = delta_content
                        reasoning_content += cleaned

                # 答案阶段聚合
                elif phase == "answer":
                    # 当 edit_content 同时包含思考结束标记与答案时，提取答案部分
                    if edit_content and "</details>\n" in edit_content:
                        content_after = edit_content.split("</details>\n")[-1]
                        if content_after:
                            final_content += content_after
                    elif delta_content:
                        final_content += delta_content

        except Exception as e:
            self.logger.error(f"❌ 非流式响应处理错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # 返回统一错误响应
            return self.handle_error(e, "非流式聚合")

        # 清理并返回
        final_content = (final_content or "").strip()
        reasoning_content = (reasoning_content or "").strip()

        # 若没有聚合到答案，但有思考内容，则保底返回思考内容
        if not final_content and reasoning_content:
            final_content = reasoning_content

        # 返回包含推理内容的标准响应（若无推理则不会携带）
        return self.create_openai_response_with_reasoning(
            chat_id,
            model,
            final_content,
            reasoning_content,
            usage_info,
        )
