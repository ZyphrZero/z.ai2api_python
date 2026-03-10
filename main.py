#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from granian import Granian

from app.admin import api as admin_api
from app.admin import routes as admin_routes
from app.core import claude, openai
from app.core.config import settings
from app.core.upstream import UpstreamClient
from app.utils.logger import setup_logger
from app.utils.reload_config import RELOAD_CONFIG

# Setup logger
logger = setup_logger(log_dir="logs", debug_mode=settings.DEBUG_LOGGING)


async def warmup_upstream_client():
    """可选预热上游适配器，提前初始化动态依赖。"""
    try:
        client = UpstreamClient()
        logger.info(
            f"✅ 上游适配器已就绪，支持 {len(client.get_supported_models())} 个模型"
        )
    except Exception as exc:
        logger.warning(f"⚠️ 上游适配器预热失败: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化 Token 数据库
    from app.services.request_log_dao import init_request_log_dao
    from app.services.token_automation import (
        run_directory_import,
        start_token_automation_scheduler,
        stop_token_automation_scheduler,
    )
    from app.services.token_dao import init_token_database

    await init_token_database()
    init_request_log_dao()

    if (
        settings.TOKEN_AUTO_IMPORT_ENABLED
        and settings.TOKEN_AUTO_IMPORT_SOURCE_DIR.strip()
    ):
        try:
            await run_directory_import(
                settings.TOKEN_AUTO_IMPORT_SOURCE_DIR,
                provider="zai",
            )
            logger.info("✅ 启动阶段已完成一次目录自动导入")
        except Exception as exc:
            logger.warning(f"⚠️ 启动阶段目录自动导入失败: {exc}")

    # 从数据库初始化认证 token 池
    from app.utils.token_pool import initialize_token_pool_from_db

    token_pool = await initialize_token_pool_from_db(
        provider="zai",
        failure_threshold=settings.TOKEN_FAILURE_THRESHOLD,
        recovery_timeout=settings.TOKEN_RECOVERY_TIMEOUT,
    )

    if not token_pool and not settings.ANONYMOUS_MODE:
        logger.warning(
            "⚠️ 未找到可用 Token 且未启用匿名模式，服务可能无法正常工作"
        )

    if settings.ANONYMOUS_MODE:
        from app.utils.guest_session_pool import initialize_guest_session_pool

        guest_pool = await initialize_guest_session_pool(
            pool_size=settings.GUEST_POOL_SIZE,
        )
        guest_status = guest_pool.get_pool_status()
        logger.info(
            "🫥 匿名会话池已就绪: "
            f"{guest_status.get('valid_sessions', 0)} 个可用会话"
        )

    await warmup_upstream_client()
    await start_token_automation_scheduler()

    yield

    logger.info("🔄 应用正在关闭...")

    await stop_token_automation_scheduler()

    if settings.ANONYMOUS_MODE:
        from app.utils.guest_session_pool import close_guest_session_pool

        await close_guest_session_pool()


# Create FastAPI app with lifespan
# root_path is used for reverse proxy path prefix (e.g., /api or /path-prefix)
app = FastAPI(lifespan=lifespan, root_path=settings.ROOT_PATH)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# 挂载web端静态文件目录
try:
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
except RuntimeError:
    # 如果 static 目录不存在，创建它
    os.makedirs("app/static/css", exist_ok=True)
    os.makedirs("app/static/js", exist_ok=True)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include API routers
app.include_router(openai.router)
app.include_router(claude.router)

# Include admin routers
app.include_router(admin_routes.router)
app.include_router(admin_api.router)


@app.options("/")
async def handle_options():
    """Handle OPTIONS requests"""
    return Response(status_code=200)


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "OpenAI Compatible API Server"}


def run_server():
    service_name = settings.SERVICE_NAME

    logger.info(f"🚀 启动 {service_name} 服务...")
    logger.info(f"📡 监听地址: 0.0.0.0:{settings.LISTEN_PORT}")
    logger.info(f"🔧 调试模式: {'开启' if settings.DEBUG_LOGGING else '关闭'}")
    logger.info(f"🔐 匿名模式: {'开启' if settings.ANONYMOUS_MODE else '关闭'}")

    try:
        Granian(
            "main:app",
            interface="asgi",
            address="0.0.0.0",
            port=settings.LISTEN_PORT,
            reload=True,  # 生产环境请关闭热重载
            process_name=service_name,  # 设置进程名称
            **RELOAD_CONFIG,  # 热重载配置
        ).serve()
    except KeyboardInterrupt:
        logger.info("🛑 收到中断信号，正在关闭服务...")
    except Exception as e:
        logger.error(f"❌ 服务启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_server()
