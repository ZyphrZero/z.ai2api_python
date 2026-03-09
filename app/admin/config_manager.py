"""Admin config metadata and helpers for the configuration console."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from dotenv import dotenv_values

from app.core.config import settings
from app.utils.env_file import update_env_file
from app.utils.logger import logger

ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")
_ENV_SOURCE_LINE_PATTERN = re.compile(
    r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=.*$"
)


@dataclass(frozen=True)
class ConfigFieldSpec:
    key: str
    label: str
    description: str
    value_type: str
    default_value: object
    input_type: str = "text"
    placeholder: str = ""
    required: bool = False
    wide: bool = False
    sensitive: bool = False
    restart_required: bool = False
    min_value: int | None = None
    max_value: int | None = None


@dataclass(frozen=True)
class ConfigSectionSpec:
    id: str
    title: str
    description: str
    fields: tuple[ConfigFieldSpec, ...]


CONFIG_SECTIONS: tuple[ConfigSectionSpec, ...] = (
    ConfigSectionSpec(
        id="access",
        title="接入与认证",
        description="控制上游接口地址、客户端鉴权和 Function Call 行为。",
        fields=(
            ConfigFieldSpec(
                key="API_ENDPOINT",
                label="上游 API 地址",
                description="代理请求实际转发到的上游聊天完成接口。",
                value_type="str",
                default_value="https://chat.z.ai/api/v2/chat/completions",
                input_type="url",
                placeholder="https://chat.z.ai/api/v2/chat/completions",
                required=True,
                wide=True,
            ),
            ConfigFieldSpec(
                key="AUTH_TOKEN",
                label="客户端认证密钥",
                description="客户端访问本服务时使用的 Bearer Token。",
                value_type="str",
                default_value="sk-your-api-key",
                input_type="password",
                placeholder="sk-your-api-key",
                wide=True,
                sensitive=True,
            ),
            ConfigFieldSpec(
                key="SKIP_AUTH_TOKEN",
                label="跳过客户端认证",
                description="仅建议开发环境使用，开启后不校验 AUTH_TOKEN。",
                value_type="bool",
                default_value=False,
            ),
            ConfigFieldSpec(
                key="TOOL_SUPPORT",
                label="启用 Function Call",
                description="允许 OpenAI 兼容接口使用工具调用能力。",
                value_type="bool",
                default_value=True,
            ),
            ConfigFieldSpec(
                key="SCAN_LIMIT",
                label="工具调用扫描限制",
                description="Function Call 扫描的最大字符数。",
                value_type="int",
                default_value=200000,
                input_type="number",
                min_value=1,
                placeholder="200000",
            ),
        ),
    ),
    ConfigSectionSpec(
        id="server",
        title="服务运行",
        description="服务监听、日志、数据库路径和反向代理前缀。",
        fields=(
            ConfigFieldSpec(
                key="SERVICE_NAME",
                label="服务名称",
                description="显示在进程列表中的服务名称。",
                value_type="str",
                default_value="api-proxy-server",
                placeholder="api-proxy-server",
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="LISTEN_PORT",
                label="监听端口",
                description="HTTP 服务监听端口。",
                value_type="int",
                default_value=8080,
                input_type="number",
                min_value=1,
                max_value=65535,
                required=True,
                restart_required=True,
                placeholder="8080",
            ),
            ConfigFieldSpec(
                key="ROOT_PATH",
                label="反向代理路径前缀",
                description="例如 /api，部署在子路径时使用。",
                value_type="str",
                default_value="",
                placeholder="/api",
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="DEBUG_LOGGING",
                label="启用调试日志",
                description="开启后会输出更详细的调试信息。",
                value_type="bool",
                default_value=False,
            ),
            ConfigFieldSpec(
                key="DB_PATH",
                label="数据库路径",
                description="SQLite 数据库文件位置。",
                value_type="str",
                default_value="tokens.db",
                placeholder="tokens.db",
                required=True,
                wide=True,
                restart_required=True,
            ),
        ),
    ),
    ConfigSectionSpec(
        id="tokens",
        title="Token 池策略",
        description="失败判定、恢复时间和自动导入、自动维护计划任务。",
        fields=(
            ConfigFieldSpec(
                key="TOKEN_FAILURE_THRESHOLD",
                label="失败阈值",
                description="连续失败多少次后将 Token 标记为不可用。",
                value_type="int",
                default_value=3,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="TOKEN_RECOVERY_TIMEOUT",
                label="恢复超时（秒）",
                description="失败 Token 重新参与调度前的等待时间。",
                value_type="int",
                default_value=1800,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_IMPORT_ENABLED",
                label="启用自动导入",
                description="按固定周期扫描服务端目录并导入 Token。",
                value_type="bool",
                default_value=False,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_IMPORT_SOURCE_DIR",
                label="自动导入目录",
                description="服务端本地目录，开启自动导入时需要可访问。",
                value_type="str",
                default_value="",
                placeholder="E:\\tokens\\input",
                wide=True,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_IMPORT_INTERVAL",
                label="自动导入间隔（秒）",
                description="自动导入的扫描周期。",
                value_type="int",
                default_value=300,
                input_type="number",
                min_value=1,
                required=True,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_MAINTENANCE_ENABLED",
                label="启用自动维护",
                description="定时执行去重、健康检查和删除失效 Token。",
                value_type="bool",
                default_value=False,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_MAINTENANCE_INTERVAL",
                label="自动维护间隔（秒）",
                description="自动维护的执行周期。",
                value_type="int",
                default_value=1800,
                input_type="number",
                min_value=1,
                required=True,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_REMOVE_DUPLICATES",
                label="自动去重",
                description="自动维护时清理重复 Token。",
                value_type="bool",
                default_value=True,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_HEALTH_CHECK",
                label="自动健康检查",
                description="自动维护时验证 Token 可用性。",
                value_type="bool",
                default_value=True,
            ),
            ConfigFieldSpec(
                key="TOKEN_AUTO_DELETE_INVALID",
                label="自动删除失效 Token",
                description="自动维护时移除已验证为无效的 Token。",
                value_type="bool",
                default_value=False,
            ),
        ),
    ),
    ConfigSectionSpec(
        id="guest",
        title="匿名 Guest 会话池",
        description="没有用户 Token 时，控制匿名会话池的容量和维护策略。",
        fields=(
            ConfigFieldSpec(
                key="ANONYMOUS_MODE",
                label="启用匿名模式",
                description="无可用用户 Token 时允许使用匿名会话。",
                value_type="bool",
                default_value=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="GUEST_POOL_SIZE",
                label="Guest 池容量",
                description="启动和维持的 guest 会话数量。",
                value_type="int",
                default_value=3,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="GUEST_SESSION_MAX_AGE",
                label="Guest 最大存活时间（秒）",
                description="单个 guest 会话的最长存活时长。",
                value_type="int",
                default_value=480,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="GUEST_POOL_MAINTENANCE_INTERVAL",
                label="Guest 维护间隔（秒）",
                description="后台维护匿名会话池的频率。",
                value_type="int",
                default_value=30,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="GUEST_CLEANUP_PARALLELISM",
                label="清理并行度",
                description="并行回收空闲 guest 会话的数量。",
                value_type="int",
                default_value=4,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="GUEST_HTTP_MAX_KEEPALIVE_CONNECTIONS",
                label="Keep-Alive 连接数",
                description="guest 会话 HTTP 连接池 keep-alive 上限。",
                value_type="int",
                default_value=20,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
            ConfigFieldSpec(
                key="GUEST_HTTP_MAX_CONNECTIONS",
                label="HTTP 总连接数",
                description="guest 会话 HTTP 连接池的总连接数上限。",
                value_type="int",
                default_value=50,
                input_type="number",
                min_value=1,
                required=True,
                restart_required=True,
            ),
        ),
    ),
    ConfigSectionSpec(
        id="models",
        title="模型映射",
        description="映射 OpenAI 兼容模型名到上游 Z.AI 实际模型名。",
        fields=(
            ConfigFieldSpec(
                key="GLM45_MODEL",
                label="GLM 4.5",
                description="标准 GLM 4.5 模型标识。",
                value_type="str",
                default_value="GLM-4.5",
                placeholder="GLM-4.5",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM45_THINKING_MODEL",
                label="GLM 4.5 Thinking",
                description="推理增强版 GLM 4.5 模型标识。",
                value_type="str",
                default_value="GLM-4.5-Thinking",
                placeholder="GLM-4.5-Thinking",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM45_SEARCH_MODEL",
                label="GLM 4.5 Search",
                description="搜索增强版 GLM 4.5 模型标识。",
                value_type="str",
                default_value="GLM-4.5-Search",
                placeholder="GLM-4.5-Search",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM45_AIR_MODEL",
                label="GLM 4.5 Air",
                description="轻量版 GLM 4.5 模型标识。",
                value_type="str",
                default_value="GLM-4.5-Air",
                placeholder="GLM-4.5-Air",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM46V_MODEL",
                label="GLM 4.6V",
                description="视觉模型标识。",
                value_type="str",
                default_value="GLM-4.6V",
                placeholder="GLM-4.6V",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM5_MODEL",
                label="GLM 5",
                description="GLM 5 模型标识。",
                value_type="str",
                default_value="GLM-5",
                placeholder="GLM-5",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM47_MODEL",
                label="GLM 4.7",
                description="GLM 4.7 主模型标识。",
                value_type="str",
                default_value="GLM-4.7",
                placeholder="GLM-4.7",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM47_THINKING_MODEL",
                label="GLM 4.7 Thinking",
                description="GLM 4.7 推理版模型标识。",
                value_type="str",
                default_value="GLM-4.7-Thinking",
                placeholder="GLM-4.7-Thinking",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM47_SEARCH_MODEL",
                label="GLM 4.7 Search",
                description="GLM 4.7 搜索版模型标识。",
                value_type="str",
                default_value="GLM-4.7-Search",
                placeholder="GLM-4.7-Search",
                required=True,
            ),
            ConfigFieldSpec(
                key="GLM47_ADVANCED_SEARCH_MODEL",
                label="GLM 4.7 Advanced Search",
                description="GLM 4.7 高级搜索模型标识。",
                value_type="str",
                default_value="GLM-4.7-advanced-search",
                placeholder="GLM-4.7-advanced-search",
                required=True,
                wide=True,
            ),
        ),
    ),
    ConfigSectionSpec(
        id="proxy",
        title="代理网络",
        description="上游访问使用的 HTTP、HTTPS 和 SOCKS5 代理。",
        fields=(
            ConfigFieldSpec(
                key="HTTP_PROXY",
                label="HTTP 代理",
                description="例如 http://127.0.0.1:7890。",
                value_type="str",
                default_value="",
                placeholder="http://127.0.0.1:7890",
                wide=True,
            ),
            ConfigFieldSpec(
                key="HTTPS_PROXY",
                label="HTTPS 代理",
                description="例如 http://127.0.0.1:7890。",
                value_type="str",
                default_value="",
                placeholder="http://127.0.0.1:7890",
                wide=True,
            ),
            ConfigFieldSpec(
                key="SOCKS5_PROXY",
                label="SOCKS5 代理",
                description="例如 socks5://127.0.0.1:1080。",
                value_type="str",
                default_value="",
                placeholder="socks5://127.0.0.1:1080",
                wide=True,
            ),
        ),
    ),
    ConfigSectionSpec(
        id="admin",
        title="后台安全",
        description="管理后台密码和会话密钥。修改后建议重新登录。",
        fields=(
            ConfigFieldSpec(
                key="ADMIN_PASSWORD",
                label="后台密码",
                description="管理后台登录密码。",
                value_type="str",
                default_value="admin123",
                input_type="password",
                placeholder="admin123",
                required=True,
                sensitive=True,
            ),
            ConfigFieldSpec(
                key="SESSION_SECRET_KEY",
                label="会话密钥",
                description="用于后台会话签名的密钥。",
                value_type="str",
                default_value="your-secret-key-change-in-production",
                input_type="password",
                placeholder="your-secret-key-change-in-production",
                required=True,
                sensitive=True,
                wide=True,
            ),
        ),
    ),
)

CONFIG_FIELD_SPECS = {
    field.key: field
    for section in CONFIG_SECTIONS
    for field in section.fields
}
MANAGED_ENV_KEYS = tuple(CONFIG_FIELD_SPECS.keys())
ReloadCallback = Callable[[], Awaitable[None]]


def read_env_content(env_path: str | Path = ENV_PATH) -> str:
    path = Path(env_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def validate_env_source(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")

    for line_number, line in enumerate(normalized.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _ENV_SOURCE_LINE_PATTERN.match(line):
            raise ValueError(
                f"第 {line_number} 行不是合法的 KEY=VALUE 格式。"
            )

    return normalized


def build_config_page_data(
    *,
    settings_obj: Any = settings,
    env_path: str | Path = ENV_PATH,
    env_example_path: str | Path = ENV_EXAMPLE_PATH,
) -> dict[str, Any]:
    env_file = Path(env_path)
    env_content = read_env_content(env_file)
    env_values = dotenv_values(env_file) if env_file.exists() else {}
    sections: list[dict[str, Any]] = []
    total_fields = 0
    overridden_fields = 0
    sensitive_fields = 0
    restart_required_fields = 0

    for section in CONFIG_SECTIONS:
        rendered_fields: list[dict[str, Any]] = []
        for field in section.fields:
            total_fields += 1
            if field.sensitive:
                sensitive_fields += 1
            if field.restart_required:
                restart_required_fields += 1

            is_overridden = field.key in env_values
            if is_overridden:
                overridden_fields += 1

            value = getattr(settings_obj, field.key, field.default_value)
            if value is None:
                value = ""

            rendered_fields.append(
                {
                    "key": field.key,
                    "label": field.label,
                    "description": field.description,
                    "value_type": field.value_type,
                    "value": value,
                    "input_type": field.input_type,
                    "placeholder": field.placeholder,
                    "required": field.required,
                    "wide": field.wide,
                    "sensitive": field.sensitive,
                    "restart_required": field.restart_required,
                    "min_value": field.min_value,
                    "max_value": field.max_value,
                    "source_label": ".env" if is_overridden else "默认值",
                    "source_badge_class": (
                        "bg-emerald-50 text-emerald-700 ring-emerald-200"
                        if is_overridden
                        else "bg-slate-100 text-slate-600 ring-slate-200"
                    ),
                }
            )

        sections.append(
            {
                "id": section.id,
                "title": section.title,
                "description": section.description,
                "fields": rendered_fields,
                "field_count": len(rendered_fields),
            }
        )

    return {
        "sections": sections,
        "env_content": env_content,
        "overview": {
            "total_sections": len(CONFIG_SECTIONS),
            "total_fields": total_fields,
            "overridden_fields": overridden_fields,
            "default_fields": total_fields - overridden_fields,
            "sensitive_fields": sensitive_fields,
            "restart_required_fields": restart_required_fields,
            "env_exists": env_file.exists(),
            "env_path": str(env_file.resolve()),
            "env_line_count": len(env_content.splitlines()) if env_content else 0,
            "example_exists": Path(env_example_path).exists(),
        },
    }


def build_form_updates(form_data: Mapping[str, Any]) -> dict[str, object]:
    updates: dict[str, object] = {}

    for key in MANAGED_ENV_KEYS:
        field = CONFIG_FIELD_SPECS[key]

        if field.value_type == "bool":
            updates[key] = key in form_data
            continue

        raw_value = str(form_data.get(key, "") or "").strip()
        if field.required and raw_value == "":
            raise ValueError(f"{field.label} 不能为空。")

        if field.value_type == "int":
            try:
                parsed = int(raw_value)
            except ValueError as exc:
                raise ValueError(f"{field.label} 必须是整数。") from exc

            if field.min_value is not None and parsed < field.min_value:
                raise ValueError(
                    f"{field.label} 不能小于 {field.min_value}。"
                )
            if field.max_value is not None and parsed > field.max_value:
                raise ValueError(
                    f"{field.label} 不能大于 {field.max_value}。"
                )
            updates[key] = parsed
            continue

        updates[key] = raw_value

    return updates


async def _apply_env_change(
    writer: Callable[[Path], None],
    *,
    reload_callback: ReloadCallback,
    env_path: str | Path = ENV_PATH,
) -> None:
    path = Path(env_path)
    had_existing_file = path.exists()
    previous_content = read_env_content(path) if had_existing_file else ""

    try:
        writer(path)
        await reload_callback()
    except Exception:
        if had_existing_file:
            path.write_text(previous_content, encoding="utf-8")
        elif path.exists():
            path.unlink()

        try:
            await reload_callback()
        except Exception as restore_exc:
            logger.warning(f"⚠️ 回滚配置后重新加载失败: {restore_exc}")
        raise


async def save_form_config(
    form_data: Mapping[str, Any],
    *,
    reload_callback: ReloadCallback,
    env_path: str | Path = ENV_PATH,
) -> dict[str, object]:
    updates = build_form_updates(form_data)

    async def _reload() -> None:
        await reload_callback()

    def _writer(target_path: Path) -> None:
        update_env_file(updates, env_path=target_path)

    await _apply_env_change(_writer, reload_callback=_reload, env_path=env_path)
    return updates


async def save_source_config(
    env_content: str,
    *,
    reload_callback: ReloadCallback,
    env_path: str | Path = ENV_PATH,
) -> None:
    normalized = validate_env_source(env_content)

    def _writer(target_path: Path) -> None:
        content = normalized.rstrip("\n")
        target_path.write_text(
            f"{content}\n" if content else "",
            encoding="utf-8",
        )

    await _apply_env_change(
        _writer,
        reload_callback=reload_callback,
        env_path=env_path,
    )


async def reset_env_to_example(
    *,
    reload_callback: ReloadCallback,
    env_path: str | Path = ENV_PATH,
    env_example_path: str | Path = ENV_EXAMPLE_PATH,
) -> None:
    example_path = Path(env_example_path)
    if not example_path.exists():
        raise FileNotFoundError(".env.example 不存在")

    example_content = example_path.read_text(encoding="utf-8")

    def _writer(target_path: Path) -> None:
        content = example_content.rstrip("\n")
        target_path.write_text(
            f"{content}\n" if content else "",
            encoding="utf-8",
        )

    await _apply_env_change(
        _writer,
        reload_callback=reload_callback,
        env_path=env_path,
    )
