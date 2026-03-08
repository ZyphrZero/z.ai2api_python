#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""

    # API Configuration
    API_ENDPOINT: str = "https://chat.z.ai/api/v2/chat/completions"
    
    # Authentication
    AUTH_TOKEN: Optional[str] = os.getenv("AUTH_TOKEN")

    # Token池配置
    TOKEN_FAILURE_THRESHOLD: int = int(os.getenv("TOKEN_FAILURE_THRESHOLD", "3"))  # 失败3次后标记为不可用
    TOKEN_RECOVERY_TIMEOUT: int = int(os.getenv("TOKEN_RECOVERY_TIMEOUT", "1800"))  # 30分钟后重试失败的token

    # Model Configuration
    GLM45_MODEL: str = os.getenv("GLM45_MODEL", "GLM-4.5")
    GLM45_THINKING_MODEL: str = os.getenv("GLM45_THINKING_MODEL", "GLM-4.5-Thinking")
    GLM45_SEARCH_MODEL: str = os.getenv("GLM45_SEARCH_MODEL", "GLM-4.5-Search")
    GLM45_AIR_MODEL: str = os.getenv("GLM45_AIR_MODEL", "GLM-4.5-Air")
    GLM46V_MODEL: str = os.getenv("GLM46V_MODEL", "GLM-4.6V")
    GLM5_MODEL: str = os.getenv("GLM5_MODEL", "GLM-5")
    GLM47_MODEL: str = os.getenv("GLM47_MODEL", "GLM-4.7")
    GLM47_THINKING_MODEL: str = os.getenv("GLM47_THINKING_MODEL", "GLM-4.7-Thinking")
    GLM47_SEARCH_MODEL: str = os.getenv("GLM47_SEARCH_MODEL", "GLM-4.7-Search")
    GLM47_ADVANCED_SEARCH_MODEL: str = os.getenv("GLM47_ADVANCED_SEARCH_MODEL", "GLM-4.7-advanced-search")

    # Server Configuration
    LISTEN_PORT: int = int(os.getenv("LISTEN_PORT", "8080"))
    DEBUG_LOGGING: bool = os.getenv("DEBUG_LOGGING", "true").lower() == "true"
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "api-proxy-server")
    ROOT_PATH: str = os.getenv("ROOT_PATH", "")  # For Nginx reverse proxy path prefix, e.g., "/api" or "/path-prefix"

    ANONYMOUS_MODE: bool = os.getenv("ANONYMOUS_MODE", "true").lower() == "true"
    GUEST_POOL_SIZE: int = int(os.getenv("GUEST_POOL_SIZE", "3"))
    GUEST_SESSION_MAX_AGE: int = int(os.getenv("GUEST_SESSION_MAX_AGE", "480"))
    GUEST_POOL_MAINTENANCE_INTERVAL: int = int(
        os.getenv("GUEST_POOL_MAINTENANCE_INTERVAL", "30")
    )
    GUEST_CLEANUP_PARALLELISM: int = int(
        os.getenv("GUEST_CLEANUP_PARALLELISM", "4")
    )
    GUEST_HTTP_MAX_KEEPALIVE_CONNECTIONS: int = int(
        os.getenv("GUEST_HTTP_MAX_KEEPALIVE_CONNECTIONS", "20")
    )
    GUEST_HTTP_MAX_CONNECTIONS: int = int(
        os.getenv("GUEST_HTTP_MAX_CONNECTIONS", "50")
    )
    TOOL_SUPPORT: bool = os.getenv("TOOL_SUPPORT", "true").lower() == "true"
    SCAN_LIMIT: int = int(os.getenv("SCAN_LIMIT", "200000"))
    SKIP_AUTH_TOKEN: bool = os.getenv("SKIP_AUTH_TOKEN", "false").lower() == "true"

    # Proxy Configuration
    HTTP_PROXY: Optional[str] = os.getenv("HTTP_PROXY")  # HTTP代理,格式: http://user:pass@host:port 或 http://host:port
    HTTPS_PROXY: Optional[str] = os.getenv("HTTPS_PROXY")  # HTTPS代理,格式同上
    SOCKS5_PROXY: Optional[str] = os.getenv("SOCKS5_PROXY")  # SOCKS5代理,格式: socks5://user:pass@host:port

    # Admin Panel Authentication
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123")  # 管理后台密码
    SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "your-secret-key-change-in-production")  # Session 密钥
    DB_PATH: str = os.getenv("DB_PATH", "tokens.db")

    class Config:
        env_file = ".env"
        extra = "ignore"  # 忽略额外字段，防止环境变量中的未知字段导致验证错误


settings = Settings()
