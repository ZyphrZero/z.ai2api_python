import pytest

from app.services.token_automation import run_token_maintenance
from app.services.token_dao import TokenDAO
from app.utils.token_pool import ZAITokenValidator


@pytest.mark.asyncio
async def test_run_token_maintenance_deletes_invalid_tokens_after_validation(
    tmp_path,
    monkeypatch,
):
    dao = TokenDAO(str(tmp_path / "tokens.db"))
    await dao.init_database()

    await dao.add_token("zai", "token-valid", validate=False)
    await dao.add_token("zai", "token-guest", validate=False)
    await dao.add_token("zai", "token-invalid", validate=False)

    async def fake_validate_token(cls, token):
        mapping = {
            "token-valid": ("user", True, None),
            "token-guest": ("guest", False, "guest token"),
            "token-invalid": ("unknown", False, "token expired"),
        }
        return mapping[token]

    monkeypatch.setattr(
        ZAITokenValidator,
        "validate_token",
        classmethod(fake_validate_token),
    )

    summary = await run_token_maintenance(
        provider="zai",
        remove_duplicates=False,
        run_health_check=False,
        delete_invalid_tokens=True,
        dao=dao,
        pool=None,
    )

    assert summary.checked_count == 3
    assert summary.valid_count == 1
    assert summary.guest_count == 1
    assert summary.invalid_count == 1
    assert summary.deleted_invalid_count == 2

    remaining_tokens = await dao.get_tokens_by_provider("zai", enabled_only=False)
    assert [token["token"] for token in remaining_tokens] == ["token-valid"]
    assert remaining_tokens[0]["token_type"] == "user"
