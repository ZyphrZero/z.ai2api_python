"""
管理后台路由模块
"""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from datetime import datetime
import os

from app.admin.auth import require_auth

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页面"""
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def dashboard(request: Request):
    """仪表盘首页"""
    from app.utils.token_pool import get_token_pool
    from app.services.token_dao import get_token_dao

    token_pool = get_token_pool()
    dao = get_token_dao()

    # 统计 Token 池状态（内存中）
    if token_pool:
        pool_status = token_pool.get_pool_status()
        available_tokens = pool_status.get("available_tokens", 0)
        total_tokens = pool_status.get("total_tokens", 0)
        healthy_tokens = pool_status.get("healthy_tokens", 0)
        user_tokens = pool_status.get("user_tokens", 0)
        guest_tokens = pool_status.get("guest_tokens", 0)
    else:
        available_tokens = 0
        total_tokens = 0
        healthy_tokens = 0
        user_tokens = 0
        guest_tokens = 0

    # 基础统计信息
    stats = {
        "uptime": "N/A",
        "total_requests": 0,
        "success_rate": 0,
        "available_tokens": available_tokens,
        "total_tokens": total_tokens,
        "healthy_tokens": healthy_tokens,
        "user_tokens": user_tokens,
        "guest_tokens": guest_tokens,
    }

    context = {
        "request": request,
        "stats": stats,
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    return templates.TemplateResponse("index.html", context)


@router.get("/config", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def config_page(request: Request):
    """配置管理页面"""
    from app.core.config import settings

    # 读取 .env 文件内容
    env_content = ""
    try:
        with open(".env", "r", encoding="utf-8") as f:
            env_content = f.read()
    except FileNotFoundError:
        env_content = "# .env 文件不存在"

    context = {
        "request": request,
        "config": {
            "SERVICE_NAME": settings.SERVICE_NAME,
            "LISTEN_PORT": settings.LISTEN_PORT,
            "DEBUG_LOGGING": settings.DEBUG_LOGGING,
            "ANONYMOUS_MODE": settings.ANONYMOUS_MODE,
            "AUTH_TOKEN": settings.AUTH_TOKEN,
            "SKIP_AUTH_TOKEN": settings.SKIP_AUTH_TOKEN,
            "TOOL_SUPPORT": settings.TOOL_SUPPORT,
            "TOKEN_FAILURE_THRESHOLD": settings.TOKEN_FAILURE_THRESHOLD,
            "TOKEN_RECOVERY_TIMEOUT": settings.TOKEN_RECOVERY_TIMEOUT,
            "SCAN_LIMIT": settings.SCAN_LIMIT,
        },
        "env_content": env_content,
    }
    return templates.TemplateResponse("config.html", context)


@router.get("/monitor", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def monitor_page(request: Request):
    """服务监控页面"""
    context = {
        "request": request,
    }
    return templates.TemplateResponse("monitor.html", context)


@router.get("/tokens", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def tokens_page(request: Request):
    """Token 管理页面"""
    context = {
        "request": request,
    }
    return templates.TemplateResponse("tokens.html", context)
