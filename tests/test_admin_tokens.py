from urllib.parse import urlencode

import pytest
from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request

from app.admin import api as admin_api
from app.core.config import settings
from app.services.token_automation import TokenMaintenanceSummary
from app.services.token_importer import TokenImportSummary


def _make_form_request(path: str, data: dict[str, str] | None = None) -> Request:
    encoded = urlencode(data or {}, doseq=True).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (
                b"content-type",
                b"application/x-www-form-urlencoded",
            )
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_import_directory_uses_configured_source_dir_when_form_empty(
    tmp_path,
    monkeypatch,
):
    source_dir = tmp_path / "tokens"
    source_dir.mkdir()
    monkeypatch.setattr(
        settings,
        "TOKEN_AUTO_IMPORT_SOURCE_DIR",
        str(source_dir),
    )

    called: dict[str, object] = {}

    async def fake_run_directory_import(
        source_dir_arg,
        *,
        provider,
        validate,
    ):
        called["source_dir"] = source_dir_arg
        called["provider"] = provider
        called["validate"] = validate
        return TokenImportSummary(
            source_dir=str(source_dir),
            scanned_files=1,
            imported_count=1,
            duplicate_count=0,
            invalid_json_count=0,
            missing_token_count=0,
            invalid_token_count=0,
        )

    import app.services.token_automation as token_automation

    monkeypatch.setattr(
        token_automation,
        "run_directory_import",
        fake_run_directory_import,
    )

    response = await admin_api.import_tokens_from_directory_api(
        _make_form_request("/admin/api/tokens/import-directory"),
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert called["source_dir"] == str(source_dir)
    assert called["provider"] == "zai"
    assert called["validate"] is True
    assert "导入成功" in body


@pytest.mark.asyncio
async def test_run_maintenance_uses_configured_actions_when_form_empty(
    monkeypatch,
):
    monkeypatch.setattr(settings, "TOKEN_AUTO_REMOVE_DUPLICATES", True)
    monkeypatch.setattr(settings, "TOKEN_AUTO_HEALTH_CHECK", False)
    monkeypatch.setattr(settings, "TOKEN_AUTO_DELETE_INVALID", True)

    called: dict[str, object] = {}

    async def fake_run_token_maintenance(
        *,
        provider,
        remove_duplicates,
        run_health_check,
        delete_invalid_tokens,
    ):
        called["provider"] = provider
        called["remove_duplicates"] = remove_duplicates
        called["run_health_check"] = run_health_check
        called["delete_invalid_tokens"] = delete_invalid_tokens
        return TokenMaintenanceSummary(
            provider=provider,
            checked_count=2,
            duplicate_removed_count=1,
            valid_count=1,
            guest_count=0,
            invalid_count=1,
            deleted_invalid_count=1,
        )

    import app.services.token_automation as token_automation

    monkeypatch.setattr(
        token_automation,
        "run_token_maintenance",
        fake_run_token_maintenance,
    )

    response = await admin_api.run_token_maintenance_api(
        _make_form_request("/admin/api/tokens/maintenance/run"),
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert called["provider"] == "zai"
    assert called["remove_duplicates"] is True
    assert called["run_health_check"] is False
    assert called["delete_invalid_tokens"] is True
    assert "维护完成" in body


def test_tokens_template_compiles():
    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("tokens.html")

    assert template is not None
