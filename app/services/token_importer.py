"""本地目录 token 导入服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.services.token_dao import TokenDAO, get_token_dao
from app.utils.logger import logger


@dataclass(frozen=True)
class TokenImportSummary:
    source_dir: str
    scanned_files: int
    imported_count: int
    duplicate_count: int
    invalid_json_count: int
    missing_token_count: int
    invalid_token_count: int

    @property
    def failed_count(self) -> int:
        return (
            self.duplicate_count
            + self.invalid_json_count
            + self.missing_token_count
            + self.invalid_token_count
        )


def _load_token_payload(file_path: Path) -> dict:
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc


async def import_tokens_from_directory(
    source_dir: str | Path,
    *,
    provider: str = "zai",
    validate: bool = True,
    dao: Optional[TokenDAO] = None,
) -> TokenImportSummary:
    """
    从本地目录导入 token。

    目录中的每个 JSON 文件应至少包含 `token` 字段。
    """
    source_path = Path(source_dir).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"导入目录不存在: {source_path}")
    if not source_path.is_dir():
        raise NotADirectoryError(f"导入路径不是目录: {source_path}")

    token_dao = dao or get_token_dao()
    token_files = sorted(source_path.rglob("*.json"))
    seen_tokens: set[str] = set()
    imported_count = 0
    duplicate_count = 0
    invalid_json_count = 0
    missing_token_count = 0
    invalid_token_count = 0

    for file_path in token_files:
        try:
            payload = _load_token_payload(file_path)
        except ValueError as exc:
            invalid_json_count += 1
            logger.warning(f"⚠️ 跳过无效 JSON 文件: {file_path} - {exc}")
            continue

        if not isinstance(payload, dict):
            invalid_json_count += 1
            logger.warning(f"⚠️ 跳过非对象 JSON 文件: {file_path}")
            continue

        token = str(payload.get("token") or "").strip()
        email = str(payload.get("email") or "").strip()
        if not token:
            missing_token_count += 1
            logger.warning(f"⚠️ 文件缺少 token 字段: {file_path}")
            continue

        if token in seen_tokens:
            duplicate_count += 1
            logger.info(f"↩️ 跳过本批次重复 Token: {file_path.name}")
            continue
        seen_tokens.add(token)

        existing = await token_dao.get_token_by_value(provider, token)
        if existing is not None:
            duplicate_count += 1
            logger.info(
                "↩️ Token 已存在，跳过导入: {} ({})",
                file_path.name,
                email or "unknown",
            )
            continue

        token_id = await token_dao.add_token(
            provider=provider,
            token=token,
            token_type="user",
            validate=validate,
        )
        if token_id is None:
            invalid_token_count += 1
            logger.warning(f"⚠️ Token 导入失败: {file_path.name} ({email or 'unknown'})")
            continue

        imported_count += 1
        logger.info(f"✅ 已导入 Token: {file_path.name} ({email or 'unknown'})")

    summary = TokenImportSummary(
        source_dir=str(source_path),
        scanned_files=len(token_files),
        imported_count=imported_count,
        duplicate_count=duplicate_count,
        invalid_json_count=invalid_json_count,
        missing_token_count=missing_token_count,
        invalid_token_count=invalid_token_count,
    )
    logger.info(
        "✅ Token 目录导入完成: "
        "scanned={}, imported={}, duplicate={}, invalid_json={}, "
        "missing_token={}, invalid_token={}",
        summary.scanned_files,
        summary.imported_count,
        summary.duplicate_count,
        summary.invalid_json_count,
        summary.missing_token_count,
        summary.invalid_token_count,
    )
    return summary
