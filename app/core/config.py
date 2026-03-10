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
    TOKEN_FAILURE_THRESHOLD: int = int(
        os.getenv("TOKEN_FAILURE_THRESHOLD", "3")
    )
    TOKEN_RECOVERY_TIMEOUT: int = int(
        os.getenv("TOKEN_RECOVERY_TIMEOUT", "1800")
    )
    TOKEN_AUTO_IMPORT_ENABLED: bool = (
        os.getenv("TOKEN_AUTO_IMPORT_ENABLED", "false").lower() == "true"
    )
    TOKEN_AUTO_IMPORT_SOURCE_DIR: str = os.getenv("TOKEN_AUTO_IMPORT_SOURCE_DIR", "")
    TOKEN_AUTO_IMPORT_INTERVAL: int = int(
        os.getenv("TOKEN_AUTO_IMPORT_INTERVAL", "300")
    )
    TOKEN_AUTO_MAINTENANCE_ENABLED: bool = (
        os.getenv("TOKEN_AUTO_MAINTENANCE_ENABLED", "false").lower() == "true"
    )
    TOKEN_AUTO_MAINTENANCE_INTERVAL: int = int(
        os.getenv("TOKEN_AUTO_MAINTENANCE_INTERVAL", "1800")
    )
    TOKEN_AUTO_REMOVE_DUPLICATES: bool = (
        os.getenv("TOKEN_AUTO_REMOVE_DUPLICATES", "true").lower() == "true"
    )
    TOKEN_AUTO_HEALTH_CHECK: bool = (
        os.getenv("TOKEN_AUTO_HEALTH_CHECK", "true").lower() == "true"
    )
    TOKEN_AUTO_DELETE_INVALID: bool = (
        os.getenv("TOKEN_AUTO_DELETE_INVALID", "false").lower() == "true"
    )

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
    GLM47_ADVANCED_SEARCH_MODEL: str = os.getenv(
        "GLM47_ADVANCED_SEARCH_MODEL",
        "GLM-4.7-advanced-search",
    )

    # Server Configuration
    LISTEN_PORT: int = int(os.getenv("LISTEN_PORT", "8080"))
    DEBUG_LOGGING: bool = os.getenv("DEBUG_LOGGING", "true").lower() == "true"
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "api-proxy-server")
    ROOT_PATH: str = os.getenv("ROOT_PATH", "")

    ANONYMOUS_MODE: bool = os.getenv("ANONYMOUS_MODE", "true").lower() == "true"
    GUEST_POOL_SIZE: int = int(os.getenv("GUEST_POOL_SIZE", "3"))
    TOOL_SUPPORT: bool = os.getenv("TOOL_SUPPORT", "true").lower() == "true"
    SCAN_LIMIT: int = int(os.getenv("SCAN_LIMIT", "200000"))
    SKIP_AUTH_TOKEN: bool = os.getenv("SKIP_AUTH_TOKEN", "false").lower() == "true"

    # Proxy Configuration
    HTTP_PROXY: Optional[str] = os.getenv("HTTP_PROXY")
    HTTPS_PROXY: Optional[str] = os.getenv("HTTPS_PROXY")
    SOCKS5_PROXY: Optional[str] = os.getenv("SOCKS5_PROXY")

    # Admin Panel Authentication
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123")
    SESSION_SECRET_KEY: str = os.getenv(
        "SESSION_SECRET_KEY",
        "your-secret-key-change-in-production",
    )
    DB_PATH: str = os.getenv("DB_PATH", "tokens.db")

    class Config:
        env_file = ".env"
        extra = "ignore"  # 忽略额外字段，防止环境变量中的未知字段导致验证错误


settings = Settings()
