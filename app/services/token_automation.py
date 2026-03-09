"""Background automation for token import and maintenance."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
from app.services.token_dao import TokenDAO, get_token_dao
from app.services.token_importer import TokenImportSummary, import_tokens_from_directory
from app.utils.logger import logger
from app.utils.token_pool import TokenPool, get_token_pool

DEFAULT_TOKEN_PROVIDER = "zai"
_AUTO_IMPORT_LOCK = asyncio.Lock()
_AUTO_MAINTENANCE_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class TokenMaintenanceSummary:
    provider: str
    checked_count: int = 0
    duplicate_removed_count: int = 0
    valid_count: int = 0
    guest_count: int = 0
    invalid_count: int = 0
    deleted_invalid_count: int = 0


async def run_directory_import(
    source_dir: str,
    *,
    provider: str = DEFAULT_TOKEN_PROVIDER,
    validate: bool = True,
    dao: Optional[TokenDAO] = None,
    pool: Optional[TokenPool] = None,
) -> TokenImportSummary:
    """Import tokens from a configured directory and refresh the pool if needed."""
    if _AUTO_IMPORT_LOCK.locked():
        raise RuntimeError("目录导入任务正在执行，请稍后再试")

    async with _AUTO_IMPORT_LOCK:
        summary = await import_tokens_from_directory(
            source_dir,
            provider=provider,
            validate=validate,
            dao=dao,
        )

        active_pool = pool if pool is not None else get_token_pool()
        if active_pool and summary.imported_count > 0:
            await active_pool.sync_from_database(provider)
            logger.info("✅ 目录导入后已同步 Token 池")

        return summary


async def run_token_maintenance(
    *,
    provider: str = DEFAULT_TOKEN_PROVIDER,
    remove_duplicates: bool = True,
    run_health_check: bool = True,
    delete_invalid_tokens: bool = False,
    dao: Optional[TokenDAO] = None,
    pool: Optional[TokenPool] = None,
) -> TokenMaintenanceSummary:
    """Run dedupe, validation, and invalid-token cleanup as one maintenance cycle."""
    if _AUTO_MAINTENANCE_LOCK.locked():
        raise RuntimeError("Token 自动维护任务正在执行，请稍后再试")

    token_dao = dao or get_token_dao()
    duplicate_removed_count = 0
    checked_count = 0
    valid_count = 0
    guest_count = 0
    invalid_count = 0
    deleted_invalid_count = 0

    async with _AUTO_MAINTENANCE_LOCK:
        if remove_duplicates:
            duplicate_removed_count = await token_dao.remove_duplicate_tokens(provider)

        should_validate = run_health_check or delete_invalid_tokens
        invalid_token_ids: list[int] = []

        if should_validate:
            validation_result = await token_dao.validate_tokens_detailed(provider)
            checked_count = int(validation_result.get("checked", 0) or 0)
            valid_count = int(validation_result.get("valid", 0) or 0)
            guest_count = int(validation_result.get("guest", 0) or 0)
            invalid_count = int(validation_result.get("invalid", 0) or 0)
            invalid_token_ids = list(
                validation_result.get("invalid_token_ids", []) or []
            )

        if delete_invalid_tokens and invalid_token_ids:
            deleted_invalid_count = await token_dao.delete_tokens_by_ids(
                invalid_token_ids
            )

        active_pool = pool if pool is not None else get_token_pool()
        if active_pool:
            await active_pool.sync_from_database(provider)
            logger.info("✅ Token 维护后已同步 Token 池")

    return TokenMaintenanceSummary(
        provider=provider,
        checked_count=checked_count,
        duplicate_removed_count=duplicate_removed_count,
        valid_count=valid_count,
        guest_count=guest_count,
        invalid_count=invalid_count,
        deleted_invalid_count=deleted_invalid_count,
    )


class TokenAutomationScheduler:
    """Run token import and maintenance loops in the application background."""

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._import_warning: Optional[str] = None
        self._maintenance_warning: Optional[str] = None

    async def start(self) -> None:
        if self._tasks:
            return

        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(
                self._auto_import_loop(),
                name="token-auto-import",
            ),
            asyncio.create_task(
                self._auto_maintenance_loop(),
                name="token-auto-maintenance",
            ),
        ]
        logger.info("✅ Token 自动任务调度器已启动")

    async def stop(self) -> None:
        if not self._tasks:
            return

        self._stop_event.set()
        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._import_warning = None
        self._maintenance_warning = None
        logger.info("🛑 Token 自动任务调度器已停止")

    async def _auto_import_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = 15
            try:
                if settings.TOKEN_AUTO_IMPORT_ENABLED:
                    wait_seconds = max(int(settings.TOKEN_AUTO_IMPORT_INTERVAL), 30)
                    source_dir = settings.TOKEN_AUTO_IMPORT_SOURCE_DIR.strip()
                    if not source_dir:
                        self._log_import_warning_once(
                            "已启用自动导入，但未配置导入目录"
                        )
                    else:
                        self._import_warning = None
                        summary = await run_directory_import(
                            source_dir,
                            provider=DEFAULT_TOKEN_PROVIDER,
                        )
                        logger.info(
                            "🔄 自动导入完成: scanned={} imported={} duplicate={} invalid={}",
                            summary.scanned_files,
                            summary.imported_count,
                            summary.duplicate_count,
                            summary.invalid_json_count + summary.invalid_token_count,
                        )
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                logger.info(f"⏭️ 跳过本轮自动导入: {exc}")
            except (FileNotFoundError, NotADirectoryError) as exc:
                self._log_import_warning_once(str(exc))
            except Exception as exc:
                logger.exception(f"❌ 自动导入 Token 失败: {exc}")

            await self._wait_or_stop(wait_seconds)

    async def _auto_maintenance_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = 15
            try:
                if settings.TOKEN_AUTO_MAINTENANCE_ENABLED:
                    wait_seconds = max(
                        int(settings.TOKEN_AUTO_MAINTENANCE_INTERVAL),
                        30,
                    )
                    if not self._has_enabled_maintenance_action():
                        self._log_maintenance_warning_once(
                            "已启用自动维护，但未选择任何维护动作"
                        )
                    else:
                        self._maintenance_warning = None
                        summary = await run_token_maintenance(
                            provider=DEFAULT_TOKEN_PROVIDER,
                            remove_duplicates=settings.TOKEN_AUTO_REMOVE_DUPLICATES,
                            run_health_check=settings.TOKEN_AUTO_HEALTH_CHECK,
                            delete_invalid_tokens=settings.TOKEN_AUTO_DELETE_INVALID,
                        )
                        logger.info(
                            "🧹 自动维护完成: dedupe={} checked={} valid={} guest={} invalid={} deleted={}",
                            summary.duplicate_removed_count,
                            summary.checked_count,
                            summary.valid_count,
                            summary.guest_count,
                            summary.invalid_count,
                            summary.deleted_invalid_count,
                        )
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                logger.info(f"⏭️ 跳过本轮自动维护: {exc}")
            except Exception as exc:
                logger.exception(f"❌ Token 自动维护失败: {exc}")

            await self._wait_or_stop(wait_seconds)

    async def _wait_or_stop(self, timeout: int) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return

    def _has_enabled_maintenance_action(self) -> bool:
        return any(
            (
                settings.TOKEN_AUTO_REMOVE_DUPLICATES,
                settings.TOKEN_AUTO_HEALTH_CHECK,
                settings.TOKEN_AUTO_DELETE_INVALID,
            )
        )

    def _log_import_warning_once(self, message: str) -> None:
        if self._import_warning == message:
            return
        self._import_warning = message
        logger.warning(f"⚠️ {message}")

    def _log_maintenance_warning_once(self, message: str) -> None:
        if self._maintenance_warning == message:
            return
        self._maintenance_warning = message
        logger.warning(f"⚠️ {message}")


_scheduler: Optional[TokenAutomationScheduler] = None


def get_token_automation_scheduler() -> TokenAutomationScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TokenAutomationScheduler()
    return _scheduler


async def start_token_automation_scheduler() -> None:
    await get_token_automation_scheduler().start()


async def stop_token_automation_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    await _scheduler.stop()
