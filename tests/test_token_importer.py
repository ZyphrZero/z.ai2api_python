import json

import pytest

from app.services.token_dao import TokenDAO
from app.services.token_importer import import_tokens_from_directory


@pytest.mark.asyncio
async def test_import_tokens_from_directory_handles_duplicates_and_invalid_files(
    tmp_path,
):
    source_dir = tmp_path / "source_tokens"
    source_dir.mkdir()

    (source_dir / "token_valid_1.json").write_text(
        json.dumps(
            {
                "email": "alpha@example.com",
                "token": "token-alpha",
                "token_source": "context.cookie:token",
            }
        ),
        encoding="utf-8",
    )
    (source_dir / "token_valid_2.json").write_text(
        json.dumps(
            {
                "email": "beta@example.com",
                "token": "token-beta",
                "token_source": "context.cookie:token",
            }
        ),
        encoding="utf-8",
    )
    (source_dir / "token_duplicate.json").write_text(
        json.dumps(
            {
                "email": "alpha-dup@example.com",
                "token": "token-alpha",
            }
        ),
        encoding="utf-8",
    )
    (source_dir / "token_missing.json").write_text(
        json.dumps({"email": "missing@example.com"}),
        encoding="utf-8",
    )
    (source_dir / "token_invalid.json").write_text("{invalid json", encoding="utf-8")

    dao = TokenDAO(str(tmp_path / "tokens.db"))
    await dao.init_database()

    summary = await import_tokens_from_directory(
        source_dir,
        provider="zai",
        validate=False,
        dao=dao,
    )

    assert summary.scanned_files == 5
    assert summary.imported_count == 2
    assert summary.duplicate_count == 1
    assert summary.missing_token_count == 1
    assert summary.invalid_json_count == 1
    assert summary.invalid_token_count == 0

    tokens = await dao.get_tokens_by_provider("zai", enabled_only=False)
    imported_values = {item["token"] for item in tokens}
    assert imported_values == {"token-alpha", "token-beta"}
