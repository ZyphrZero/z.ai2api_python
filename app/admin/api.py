"""
管理后台 API 接口
用于 htmx 调用的 HTML 片段返回
"""
from datetime import datetime
from html import escape
from pathlib import Path
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.admin.auth import require_auth
from app.admin.config_manager import (
    read_env_content,
    reset_env_to_example,
    save_form_config,
    save_source_config,
)
from app.admin.stats import collect_admin_stats, normalize_trend_window
from app.services.request_log_dao import get_request_log_dao
from app.utils.logger import logger

router = APIRouter(prefix="/admin/api", tags=["admin-api"])
templates = Jinja2Templates(directory="app/templates")
DEFAULT_TOKEN_NAMESPACE = "zai"


# ==================== 认证 API ====================

@router.post("/login")
async def login(request: Request):
    """管理后台登录"""
    from app.admin.auth import create_session

    try:
        data = await request.json()
        password = data.get("password", "")

        # 创建 session
        session_token = create_session(password)

        if session_token:
            # 登录成功，设置 cookie
            response = JSONResponse({
                "success": True,
                "message": "登录成功"
            })
            response.set_cookie(
                key="admin_session",
                value=session_token,
                httponly=True,
                max_age=86400,  # 24小时
                samesite="lax"
            )
            logger.info("✅ 管理后台登录成功")
            return response
        else:
            # 密码错误
            logger.warning("❌ 管理后台登录失败：密码错误")
            return JSONResponse({
                "success": False,
                "message": "密码错误"
            }, status_code=401)

    except Exception as e:
        logger.error(f"❌ 登录异常: {e}")
        return JSONResponse({
            "success": False,
            "message": "登录失败"
        }, status_code=500)


@router.post("/logout")
async def logout(request: Request):
    """管理后台登出"""
    from app.admin.auth import delete_session, get_session_token_from_request

    session_token = get_session_token_from_request(request)
    delete_session(session_token)

    # 清除 cookie
    response = JSONResponse({
        "success": True,
        "message": "已登出"
    })
    response.delete_cookie("admin_session")
    logger.info("✅ 管理后台已登出")
    return response


async def reload_settings():
    """热重载配置（重新加载环境变量并更新 settings 对象）"""
    from dotenv import load_dotenv

    from app.core.config import settings
    from app.utils.logger import setup_logger

    # 重新加载 .env 文件
    load_dotenv(override=True)

    # 重新创建 Settings 对象并更新全局配置
    new_settings = type(settings)()

    # 更新全局 settings 的所有属性
    for field_name in new_settings.model_fields.keys():
        setattr(settings, field_name, getattr(new_settings, field_name))

    # 重新初始化 logger（使用新的 DEBUG_LOGGING 配置）
    setup_logger(log_dir="logs", debug_mode=settings.DEBUG_LOGGING)

    logger.info(f"🔄 配置已热重载 (DEBUG_LOGGING={settings.DEBUG_LOGGING})")


def _build_alert(
    message: str,
    *,
    title: str,
    level: str,
    status_code: int = 200,
) -> HTMLResponse:
    level_classes = {
        "success": "bg-green-100 border-green-400 text-green-700",
        "warning": "bg-yellow-100 border-yellow-400 text-yellow-700",
        "error": "bg-red-100 border-red-400 text-red-700",
        "info": "bg-blue-100 border-blue-400 text-blue-700",
    }
    classes = level_classes.get(level, level_classes["info"])
    safe_title = escape(title)
    safe_message = escape(message)
    return HTMLResponse(
        f"""
        <div class="{classes} border px-4 py-3 rounded relative" role="alert">
            <strong class="font-bold">{safe_title}</strong>
            <span class="block sm:inline">{safe_message}</span>
        </div>
        """,
        status_code=status_code,
    )


def _with_hx_trigger(response: HTMLResponse, event_name: str) -> HTMLResponse:
    response.headers["HX-Trigger"] = event_name
    return response


def _get_int_query_param(
    request: Request,
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: Optional[int] = None,
) -> int:
    """解析查询参数中的正整数，非法值回退到默认值。"""
    raw_value = request.query_params.get(name)
    if raw_value is None:
        return default

    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default

    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _build_pagination(
    *,
    total_items: int,
    page: int,
    page_size: int,
) -> dict:
    """构建分页上下文。"""
    total_items = max(0, int(total_items))
    page_size = max(1, int(page_size))
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    current_page = min(max(1, int(page)), total_pages)

    if total_items == 0:
        start_item = 0
        end_item = 0
    else:
        start_item = (current_page - 1) * page_size + 1
        end_item = min(total_items, current_page * page_size)

    return {
        "current_page": current_page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
        "previous_page": max(1, current_page - 1),
        "next_page": min(total_pages, current_page + 1),
        "start_item": start_item,
        "end_item": end_item,
    }


def _normalize_display_value(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())
    return normalized


def _is_redundant_source(source: str, client_name: str) -> bool:
    normalized_source = _normalize_display_value(source)
    normalized_client = _normalize_display_value(client_name)
    if not normalized_source:
        return True
    if not normalized_client:
        return False
    return normalized_source == normalized_client


def _humanize_protocol(protocol: str) -> str:
    normalized = str(protocol or "").strip().lower()
    if normalized == "openai":
        return "OpenAI"
    if normalized == "anthropic":
        return "Anthropic"
    if normalized == "unknown":
        return "Unknown"
    return normalized or "Unknown"


@router.get(
    "/dashboard/usage-trend",
    response_class=JSONResponse,
    dependencies=[Depends(require_auth)],
)
async def get_dashboard_usage_trend(request: Request):
    """返回仪表盘趋势图数据。"""
    trend_window = normalize_trend_window(
        request.query_params.get("window")
    )
    dao = get_request_log_dao()
    trend_points = await dao.get_provider_usage_trend(
        DEFAULT_TOKEN_NAMESPACE,
        window=trend_window,
    )
    return JSONResponse(
        {
            "window": trend_window,
            "points": trend_points,
        }
    )


def _validate_directory_path(source_dir: str) -> str:
    if not source_dir:
        raise ValueError("请先填写服务端可访问的本地目录路径。")

    source_path = Path(source_dir).expanduser()
    if not source_path.exists():
        raise ValueError(f"导入目录不存在: {source_path}")
    if not source_path.is_dir():
        raise ValueError(f"导入路径不是目录: {source_path}")

    return str(source_path)


@router.get("/token-pool", response_class=HTMLResponse)
async def get_token_pool_status(request: Request):
    """获取 Token 池状态（HTML 片段）"""
    from app.utils.token_pool import get_token_pool

    token_pool = get_token_pool()

    if not token_pool:
        # Token 池未初始化
        context = {
            "request": request,
            "tokens": [],
        }
        return templates.TemplateResponse("components/token_pool.html", context)

    # 获取 token 状态统计
    pool_status = token_pool.get_pool_status()
    tokens_info = []

    for idx, token_info in enumerate(pool_status.get("tokens", []), 1):
        is_available = token_info.get("is_available", False)
        is_healthy = token_info.get("is_healthy", False)

        # 确定状态和颜色
        if is_healthy:
            status = "健康"
            status_color = "bg-green-100 text-green-800"
        elif is_available:
            status = "可用"
            status_color = "bg-yellow-100 text-yellow-800"
        else:
            status = "失败"
            status_color = "bg-red-100 text-red-800"

        # 格式化最后使用时间
        last_success = token_info.get("last_success_time", 0)
        if last_success > 0:
            from datetime import datetime
            last_used = datetime.fromtimestamp(last_success).strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_used = "从未使用"

        tokens_info.append({
            "index": idx,
            "key": token_info.get("token", "")[:20] + "...",
            "status": status,
            "status_color": status_color,
            "last_used": last_used,
            "failure_count": token_info.get("failure_count", 0),
            "success_rate": token_info.get("success_rate", "0%"),
            "token_type": token_info.get("token_type", "unknown"),
        })

    context = {
        "request": request,
        "tokens": tokens_info,
    }

    return templates.TemplateResponse("components/token_pool.html", context)


@router.get("/recent-logs", response_class=HTMLResponse)
async def get_recent_logs(request: Request):
    """获取最近的请求日志（HTML 片段）"""
    dao = get_request_log_dao()
    page_size = _get_int_query_param(
        request,
        "page_size",
        12,
        maximum=50,
    )
    requested_page = _get_int_query_param(request, "page", 1, maximum=100000)
    total_count = await dao.count_logs()
    pagination = _build_pagination(
        total_items=total_count,
        page=requested_page,
        page_size=page_size,
    )

    rows = await dao.get_recent_logs(
        limit=page_size,
        offset=(pagination["current_page"] - 1) * page_size,
    )
    logs = []
    for row in rows:
        timestamp = (
            row.get("timestamp")
            or row.get("created_at")
            or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        success = bool(row.get("success"))
        status_code = int(
            row.get("status_code") or (200 if success else 500)
        )
        duration_value = float(row.get("duration") or 0.0)
        first_token_value = float(row.get("first_token_time") or 0.0)
        source = row.get("source") or "unknown"
        client_name = row.get("client_name") or "Unknown"
        provider = row.get("provider") or "-"
        source_display = (
            ""
            if _is_redundant_source(source, client_name)
            else source
        )
        provider_display = "" if provider == "zai" else provider
        logs.append(
            {
                "timestamp": timestamp,
                "endpoint": row.get("endpoint") or "-",
                "model": row.get("model") or "-",
                "provider": provider,
                "provider_display": provider_display,
                "source": source,
                "source_display": source_display,
                "protocol": row.get("protocol") or "unknown",
                "protocol_display": _humanize_protocol(
                    row.get("protocol") or "unknown"
                ),
                "client_name": client_name,
                "success": success,
                "status_code": status_code,
                "duration_display": f"{duration_value:.2f}s",
                "first_token_display": (
                    f"{first_token_value:.2f}s"
                    if first_token_value > 0
                    else "--"
                ),
                "input_tokens": int(row.get("input_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
                "cache_creation_tokens": int(
                    row.get("cache_creation_tokens") or 0
                ),
                "cache_read_tokens": int(
                    row.get("cache_read_tokens") or 0
                ),
                "error_message": row.get("error_message") or "",
            }
        )

    context = {
        "request": request,
        "logs": logs,
        "page": pagination,
    }

    return templates.TemplateResponse("components/recent_logs.html", context)


@router.post("/config/save", dependencies=[Depends(require_auth)])
async def save_config(request: Request):
    """保存结构化配置并热重载。"""
    try:
        form_data = await request.form()
        await save_form_config(
            form_data,
            reload_callback=reload_settings,
        )
        logger.info("✅ 结构化配置已保存")
        return _with_hx_trigger(
            _build_alert(
                "配置已保存并热重载，页面即将刷新。",
                title="保存成功！",
                level="success",
            ),
            "admin-config-refresh",
        )
    except ValueError as exc:
        return _build_alert(
            str(exc),
            title="校验失败！",
            level="error",
            status_code=400,
        )
    except Exception as exc:
        logger.error(f"❌ 配置保存失败: {exc}")
        return _build_alert(
            f"保存失败: {exc}",
            title="错误！",
            level="error",
            status_code=500,
        )


@router.post("/config/source", dependencies=[Depends(require_auth)])
async def save_config_source(request: Request):
    """保存 .env 源文件并热重载。"""
    try:
        form_data = await request.form()
        await save_source_config(
            str(form_data.get("env_content", "")),
            reload_callback=reload_settings,
        )
        logger.info("✅ 配置源文件已保存")
        return _with_hx_trigger(
            _build_alert(
                ".env 源文件已保存并热重载，页面即将刷新。",
                title="保存成功！",
                level="success",
            ),
            "admin-config-refresh",
        )
    except ValueError as exc:
        return _build_alert(
            str(exc),
            title="源文件校验失败！",
            level="error",
            status_code=400,
        )
    except Exception as exc:
        logger.error(f"❌ 源文件保存失败: {exc}")
        return _build_alert(
            f"源文件保存失败: {exc}",
            title="错误！",
            level="error",
            status_code=500,
        )


@router.post("/config/reset", dependencies=[Depends(require_auth)])
async def reset_config():
    """将配置重置为 .env.example 并热重载。"""
    try:
        await reset_env_to_example(reload_callback=reload_settings)
        logger.info("✅ 配置已重置为 .env.example 默认值")
        return _with_hx_trigger(
            _build_alert(
                "配置已恢复为 .env.example 默认值，页面即将刷新。",
                title="已重置！",
                level="success",
            ),
            "admin-config-refresh",
        )
    except FileNotFoundError:
        logger.error("❌ 未找到 .env.example，无法重置配置")
        return _build_alert(
            "未找到 .env.example，无法重置配置。",
            title="错误！",
            level="error",
            status_code=404,
        )
    except Exception as exc:
        logger.error(f"❌ 配置重置失败: {exc}")
        return _build_alert(
            f"重置失败: {exc}",
            title="错误！",
            level="error",
            status_code=500,
        )


@router.get("/env-preview", dependencies=[Depends(require_auth)])
async def get_env_preview():
    """获取 .env 文件预览"""
    try:
        content = read_env_content()
        if not content:
            content = "# .env 文件不存在"
        return HTMLResponse(f"<pre>{escape(content)}</pre>")
    except Exception as exc:
        return HTMLResponse(f"<pre># 读取失败: {escape(str(exc))}</pre>")


@router.get("/live-logs", response_class=HTMLResponse)
async def get_live_logs():
    """获取实时日志（最新 50 行）"""
    import os
    from datetime import datetime

    logs = []

    # 尝试读取日志文件
    log_dir = "logs"
    if os.path.exists(log_dir):
        log_files = sorted([f for f in os.listdir(log_dir) if f.endswith('.log')], reverse=True)
        if log_files:
            log_file = os.path.join(log_dir, log_files[0])
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    # 读取最后 50 行
                    lines = f.readlines()[-50:]
                    logs = lines
            except Exception as e:
                logs = [f"# [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 读取日志失败: {str(e)}"]

    if not logs:
        logs = [f"# [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 暂无日志数据"]

    html = ""
    for log in logs:
        log_line = log.strip()
        if not log_line:
            continue

        # 根据日志级别设置颜色和样式
        if "ERROR" in log_line or "CRITICAL" in log_line:
            color_class = "text-red-400 font-semibold"
            icon = "❌"
        elif "WARNING" in log_line or "WARN" in log_line:
            color_class = "text-yellow-400"
            icon = "⚠️"
        elif "SUCCESS" in log_line or "✅" in log_line:
            color_class = "text-green-400"
            icon = "✅"
        elif "INFO" in log_line:
            color_class = "text-blue-400"
            icon = "ℹ️"
        elif "DEBUG" in log_line:
            color_class = "text-gray-400 text-xs"
            icon = "🔍"
        else:
            color_class = "text-gray-300"
            icon = "•"

        # 转义 HTML 特殊字符
        log_escaped = log_line.replace('<', '&lt;').replace('>', '&gt;')

        html += f'<div class="{color_class} py-0.5 hover:bg-gray-800 px-2 rounded transition-colors">{icon} {log_escaped}</div>'

    return HTMLResponse(html)


# ==================== Token 管理 API ====================

@router.get("/tokens/list", response_class=HTMLResponse)
async def get_tokens_list(request: Request):
    """获取 Token 列表（HTML 片段）"""
    from app.services.token_dao import get_token_dao

    dao = get_token_dao()
    page_size = _get_int_query_param(
        request,
        "page_size",
        20,
        maximum=100,
    )
    requested_page = _get_int_query_param(request, "page", 1, maximum=100000)
    total_count = await dao.count_tokens_by_provider(
        DEFAULT_TOKEN_NAMESPACE,
        enabled_only=False,
    )
    pagination = _build_pagination(
        total_items=total_count,
        page=requested_page,
        page_size=page_size,
    )
    tokens = await dao.get_tokens_by_provider(
        DEFAULT_TOKEN_NAMESPACE,
        enabled_only=False,
        limit=page_size,
        offset=(pagination["current_page"] - 1) * page_size,
    )

    context = {
        "request": request,
        "tokens": tokens,
        "page": pagination,
    }

    return templates.TemplateResponse("components/token_list.html", context)


@router.post("/tokens/add")
async def add_tokens(request: Request):
    """添加 Token"""
    from app.services.token_dao import get_token_dao
    from app.utils.token_pool import get_token_pool

    form_data = await request.form()
    single_token = form_data.get("single_token", "").strip()
    bulk_tokens = form_data.get("bulk_tokens", "").strip()

    dao = get_token_dao()
    added_count = 0
    failed_count = 0

    # 添加单个 Token（带验证）
    if single_token:
        token_id = await dao.add_token(
            DEFAULT_TOKEN_NAMESPACE,
            single_token,
            validate=True,
        )
        if token_id:
            added_count += 1
        else:
            failed_count += 1

    # 批量添加 Token（带验证）
    if bulk_tokens:
        # 支持换行和逗号分隔
        tokens = []
        for line in bulk_tokens.split('\n'):
            line = line.strip()
            if ',' in line:
                tokens.extend([t.strip() for t in line.split(',') if t.strip()])
            elif line:
                tokens.append(line)

        success, failed = await dao.bulk_add_tokens(
            DEFAULT_TOKEN_NAMESPACE,
            tokens,
            validate=True,
        )
        added_count += success
        failed_count += failed

    # 同步 Token 池状态（如果有新增成功的 Token）
    if added_count > 0:
        pool = get_token_pool()
        if pool:
            await pool.sync_from_database(DEFAULT_TOKEN_NAMESPACE)
            logger.info(f"✅ Token 池已同步，新增 {added_count} 个 Token")

    # 生成响应
    if added_count > 0 and failed_count == 0:
        return HTMLResponse(f"""
        <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded relative" role="alert">
            <strong class="font-bold">成功！</strong>
            <span class="block sm:inline">已添加 {added_count} 个有效 Token</span>
        </div>
        """)
    elif added_count > 0 and failed_count > 0:
        return HTMLResponse(f"""
        <div class="bg-yellow-100 border border-yellow-400 text-yellow-700 px-4 py-3 rounded relative" role="alert">
            <strong class="font-bold">部分成功！</strong>
            <span class="block sm:inline">已添加 {added_count} 个 Token，{failed_count} 个失败（可能是重复、无效或匿名 Token）</span>
        </div>
        """)
    else:
        return HTMLResponse("""
        <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative" role="alert">
            <strong class="font-bold">失败！</strong>
            <span class="block sm:inline">所有 Token 添加失败（可能是重复、无效或匿名 Token）</span>
        </div>
        """)


@router.post("/tokens/import-directory", dependencies=[Depends(require_auth)])
async def import_tokens_from_directory_api(request: Request):
    """从本地目录导入 token 文件。"""
    from app.core.config import settings
    from app.services.token_automation import run_directory_import

    form_data = await request.form()
    source_dir = str(
        form_data.get("source_dir")
        or settings.TOKEN_AUTO_IMPORT_SOURCE_DIR
        or ""
    ).strip()
    try:
        source_dir = _validate_directory_path(source_dir)
    except ValueError as exc:
        return _build_alert(
            str(exc),
            title="导入失败！",
            level="error",
            status_code=400,
        )

    try:
        summary = await run_directory_import(
            source_dir,
            provider=DEFAULT_TOKEN_NAMESPACE,
            validate=True,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        return _build_alert(
            str(exc),
            title="导入失败！",
            level="error",
            status_code=400,
        )
    except RuntimeError as exc:
        return _build_alert(
            str(exc),
            title="导入稍后重试",
            level="warning",
            status_code=409,
        )
    except Exception as exc:
        logger.exception(f"❌ 本地目录导入 Token 失败: {exc}")
        return _build_alert(
            f"目录扫描或入库异常: {exc}",
            title="导入失败！",
            level="error",
            status_code=500,
        )

    if summary.imported_count > 0:
        title = "导入成功！" if summary.failed_count == 0 else "导入完成！"
        detail = (
            f"目录 {summary.source_dir} 共扫描 {summary.scanned_files} 个文件，"
            f"成功导入 {summary.imported_count} 个 Token，"
            f"重复 {summary.duplicate_count} 个，"
            f"无效 JSON {summary.invalid_json_count} 个，"
            f"缺少 token {summary.missing_token_count} 个，"
            f"验证失败 {summary.invalid_token_count} 个。"
        )
        return _build_alert(
            detail,
            title=title,
            level="success" if summary.failed_count == 0 else "warning",
        )

    return _build_alert(
        (
            f"目录 {summary.source_dir} 共扫描 {summary.scanned_files} 个文件，"
            f"其中重复 {summary.duplicate_count} 个，无效 JSON {summary.invalid_json_count} 个，"
            f"缺少 token {summary.missing_token_count} 个，验证失败 {summary.invalid_token_count} 个。"
        ),
        title="未导入任何 Token！",
        level="warning",
    )


@router.post("/tokens/auto-import/save", dependencies=[Depends(require_auth)])
async def save_auto_import_settings(request: Request):
    """兼容旧入口，提示用户改到配置管理页。"""
    return _build_alert(
        "自动导入配置入口已迁移到 /admin/config#tokens，当前页面仅保留手动执行入口。",
        title="入口已迁移",
        level="info",
    )


@router.post("/tokens/maintenance/save", dependencies=[Depends(require_auth)])
async def save_auto_maintenance_settings(request: Request):
    """兼容旧入口，提示用户改到配置管理页。"""
    return _build_alert(
        "自动维护配置入口已迁移到 /admin/config#tokens，当前页面仅保留手动执行入口。",
        title="入口已迁移",
        level="info",
    )


@router.post("/tokens/maintenance/run", dependencies=[Depends(require_auth)])
async def run_token_maintenance_api(request: Request):
    """立即执行一次 Token 维护。"""
    from app.core.config import settings
    from app.services.token_automation import run_token_maintenance

    form_data = await request.form()
    action_fields = (
        "auto_remove_duplicates",
        "auto_health_check",
        "auto_delete_invalid",
    )
    has_explicit_actions = any(field in form_data for field in action_fields)

    if has_explicit_actions:
        remove_duplicates = "auto_remove_duplicates" in form_data
        run_health_check = "auto_health_check" in form_data
        delete_invalid = "auto_delete_invalid" in form_data
    else:
        remove_duplicates = settings.TOKEN_AUTO_REMOVE_DUPLICATES
        run_health_check = settings.TOKEN_AUTO_HEALTH_CHECK
        delete_invalid = settings.TOKEN_AUTO_DELETE_INVALID

    if not any((remove_duplicates, run_health_check, delete_invalid)):
        return _build_alert(
            "当前没有可执行的维护动作，请先到 /admin/config#tokens 配置至少一个维护动作。",
            title="未执行维护！",
            level="warning",
            status_code=400,
        )

    try:
        summary = await run_token_maintenance(
            provider=DEFAULT_TOKEN_NAMESPACE,
            remove_duplicates=remove_duplicates,
            run_health_check=run_health_check,
            delete_invalid_tokens=delete_invalid,
        )
    except RuntimeError as exc:
        return _build_alert(
            str(exc),
            title="维护稍后重试",
            level="warning",
            status_code=409,
        )
    except Exception as exc:
        logger.exception(f"❌ 手动执行 Token 维护失败: {exc}")
        return _build_alert(
            f"Token 维护失败: {exc}",
            title="维护失败！",
            level="error",
            status_code=500,
        )

    return _build_alert(
        (
            f"本次维护共去重 {summary.duplicate_removed_count} 个，"
            f"测活 {summary.checked_count} 个（有效 {summary.valid_count} / "
            f"匿名 {summary.guest_count} / 无效 {summary.invalid_count}），"
            f"删除失效 Token {summary.deleted_invalid_count} 个。"
        ),
        title="维护完成！",
        level="success",
    )


@router.post("/tokens/toggle/{token_id}")
async def toggle_token(token_id: int, enabled: bool):
    """切换 Token 启用状态"""
    from app.services.token_dao import get_token_dao
    from app.utils.token_pool import get_token_pool

    dao = get_token_dao()
    await dao.update_token_status(token_id, enabled)

    # 同步 Token 池状态
    pool = get_token_pool()
    if pool:
        # 获取 Token 的提供商信息
        async with dao.get_connection() as conn:
            cursor = await conn.execute("SELECT provider FROM tokens WHERE id = ?", (token_id,))
            row = await cursor.fetchone()
            if row:
                provider = row[0]
                await pool.sync_from_database(provider)
                logger.info("✅ Token 池已同步")

    # 根据状态返回不同样式的按钮
    if enabled:
        button_class = "bg-green-100 text-green-800 hover:bg-green-200"
        indicator_class = "bg-green-500"
        label = "已启用"
        next_state = "false"
    else:
        button_class = "bg-red-100 text-red-800 hover:bg-red-200"
        indicator_class = "bg-red-500"
        label = "已禁用"
        next_state = "true"

    return HTMLResponse(f"""
    <button hx-post="/admin/api/tokens/toggle/{token_id}?enabled={next_state}"
            hx-swap="outerHTML"
            class="inline-flex items-center px-2.5 py-0.5 text-xs font-semibold rounded-full transition-colors {button_class}">
        <span class="h-2 w-2 rounded-full mr-1.5 {indicator_class}"></span>
        {label}
    </button>
    """)


@router.delete("/tokens/delete/{token_id}")
async def delete_token(token_id: int):
    """删除 Token"""
    from app.services.token_dao import get_token_dao
    from app.utils.token_pool import get_token_pool

    dao = get_token_dao()

    # 获取 Token 信息以确定提供商
    async with dao.get_connection() as conn:
        cursor = await conn.execute("SELECT provider FROM tokens WHERE id = ?", (token_id,))
        row = await cursor.fetchone()
        provider = row[0] if row else "zai"

    await dao.delete_token(token_id)

    # 同步 Token 池状态
    pool = get_token_pool()
    if pool:
        await pool.sync_from_database(provider)
        logger.info("✅ Token 池已同步")

    return HTMLResponse("")  # 返回空内容，让 htmx 移除元素


@router.get("/tokens/stats", response_class=HTMLResponse)
async def get_tokens_stats(request: Request):
    """获取 Token 统计信息（HTML 片段）"""
    stats_data = await collect_admin_stats(DEFAULT_TOKEN_NAMESPACE)

    context = {
        "request": request,
        "stats": stats_data,
    }

    return templates.TemplateResponse("components/token_stats.html", context)


@router.post("/tokens/validate")
async def validate_tokens():
    """批量验证 Token"""
    from app.services.token_dao import get_token_dao
    from app.utils.token_pool import get_token_pool

    dao = get_token_dao()

    # 执行批量验证
    stats = await dao.validate_all_tokens(DEFAULT_TOKEN_NAMESPACE)

    pool = get_token_pool()
    if pool:
        await pool.sync_from_database(DEFAULT_TOKEN_NAMESPACE)

    valid_count = stats.get("valid", 0)
    guest_count = stats.get("guest", 0)
    invalid_count = stats.get("invalid", 0)

    # 生成通知消息
    if guest_count > 0:
        message_class = "bg-yellow-100 border-yellow-400 text-yellow-700"
        message = f"验证完成：有效 {valid_count} 个，匿名 {guest_count} 个，无效 {invalid_count} 个。匿名 Token 已标记。"
    elif invalid_count > 0:
        message_class = "bg-blue-100 border-blue-400 text-blue-700"
        message = f"验证完成：有效 {valid_count} 个，无效 {invalid_count} 个。"
    else:
        message_class = "bg-green-100 border-green-400 text-green-700"
        message = f"验证完成：所有 {valid_count} 个 Token 均有效！"

    return HTMLResponse(f"""
    <div class="{message_class} border px-4 py-3 rounded relative" role="alert">
        <strong class="font-bold">批量验证完成！</strong>
        <span class="block sm:inline">{message}</span>
    </div>
    """)


@router.post("/tokens/validate-single/{token_id}")
async def validate_single_token(request: Request, token_id: int):
    """验证单个 Token 并返回更新后的行"""
    from app.services.token_dao import get_token_dao
    from app.utils.token_pool import get_token_pool

    dao = get_token_dao()

    # 验证 Token
    await dao.validate_and_update_token(token_id)

    pool = get_token_pool()
    if pool:
        await pool.sync_from_database(DEFAULT_TOKEN_NAMESPACE)

    # 获取更新后的 Token 信息
    async with dao.get_connection() as conn:
        cursor = await conn.execute("""
            SELECT t.*, ts.total_requests, ts.successful_requests, ts.failed_requests,
                   ts.last_success_time, ts.last_failure_time
            FROM tokens t
            LEFT JOIN token_stats ts ON t.id = ts.token_id
            WHERE t.id = ?
        """, (token_id,))
        row = await cursor.fetchone()

    if row:
        # 返回更新后的单行 HTML
        token = dict(row)
        context = {
            "request": request,
            "token": token,
        }
        # 使用单行模板渲染
        return templates.TemplateResponse("components/token_row.html", context)
    else:
        return HTMLResponse("")


@router.post("/tokens/health-check")
async def health_check_tokens():
    """执行 Token 池健康检查"""
    from app.utils.token_pool import get_token_pool

    pool = get_token_pool()

    if not pool:
        return HTMLResponse("""
        <div class="bg-yellow-100 border border-yellow-400 text-yellow-700 px-4 py-3 rounded relative" role="alert">
            <strong class="font-bold">提示！</strong>
            <span class="block sm:inline">Token 池未初始化，请重启服务。</span>
        </div>
        """)

    # 执行健康检查
    await pool.health_check_all()

    # 获取健康状态
    status = pool.get_pool_status()
    healthy_count = status.get("healthy_tokens", 0)
    total_count = status.get("total_tokens", 0)

    if healthy_count == total_count:
        message_class = "bg-green-100 border-green-400 text-green-700"
        message = f"所有 {total_count} 个 Token 均健康！"
    elif healthy_count > 0:
        message_class = "bg-blue-100 border-blue-400 text-blue-700"
        message = f"健康检查完成：{healthy_count}/{total_count} 个 Token 健康。"
    else:
        message_class = "bg-red-100 border-red-400 text-red-700"
        message = f"警告：0/{total_count} 个 Token 健康，请检查配置。"

    return HTMLResponse(f"""
    <div class="{message_class} border px-4 py-3 rounded relative" role="alert">
        <strong class="font-bold">健康检查完成！</strong>
        <span class="block sm:inline">{message}</span>
    </div>
    """)


@router.post("/tokens/sync-pool")
async def sync_token_pool():
    """手动同步 Token 池（从数据库重新加载）"""
    from app.utils.token_pool import get_token_pool

    pool = get_token_pool()

    if not pool:
        return HTMLResponse("""
        <div class="bg-yellow-100 border border-yellow-400 text-yellow-700 px-4 py-3 rounded relative" role="alert">
            <strong class="font-bold">提示！</strong>
            <span class="block sm:inline">Token 池未初始化，请重启服务。</span>
        </div>
        """)

    # 从数据库同步
    await pool.sync_from_database(DEFAULT_TOKEN_NAMESPACE)

    # 获取同步后的状态
    status = pool.get_pool_status()
    total_count = status.get("total_tokens", 0)
    available_count = status.get("available_tokens", 0)
    user_count = status.get("user_tokens", 0)

    logger.info(
        f"✅ Token 池手动同步完成，总计 {total_count} 个 Token, 可用 {available_count} 个, 认证用户 {user_count} 个"
    )

    if total_count == 0:
        message_class = "bg-yellow-100 border-yellow-400 text-yellow-700"
        message = "同步完成：当前没有可用 Token，请在数据库中启用 Token。"
    elif available_count == 0:
        message_class = "bg-orange-100 border-orange-400 text-orange-700"
        message = f"同步完成：共 {total_count} 个 Token，但无可用 Token（可能都已禁用）。"
    else:
        message_class = "bg-green-100 border-green-400 text-green-700"
        message = f"同步完成：共 {total_count} 个 Token，{available_count} 个可用，{user_count} 个认证用户。"

    return HTMLResponse(f"""
    <div class="{message_class} border px-4 py-3 rounded relative" role="alert">
        <strong class="font-bold">Token 池同步完成！</strong>
        <span class="block sm:inline">{message}</span>
    </div>
    """)
