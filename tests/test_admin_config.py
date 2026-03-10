from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request

from app.admin import api as admin_api
from app.admin.config_manager import (
    CONFIG_FIELD_SPECS,
    build_config_page_data,
    save_form_config,
    save_source_config,
    validate_env_source,
)


def _build_form_payload(**overrides):
    payload = {}

    for key, field in CONFIG_FIELD_SPECS.items():
        value = overrides[key] if key in overrides else field.default_value
        if field.value_type == "bool":
            if value:
                payload[key] = "on"
            continue
        payload[key] = "" if value is None else str(value)

    return payload


def _make_form_request(path: str, data: dict[str, str]) -> Request:
    body = urlencode(data, doseq=True).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

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
async def test_build_config_page_data_includes_sections_and_override_status(
    tmp_path,
):
    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    env_path.write_text(
        "API_ENDPOINT=https://example.com/v1/chat\nDEBUG_LOGGING=true\n",
        encoding="utf-8",
    )
    example_path.write_text("SERVICE_NAME=example\n", encoding="utf-8")

    settings_stub = SimpleNamespace(
        API_ENDPOINT="https://example.com/v1/chat",
        DEBUG_LOGGING=True,
        GLM5_MODEL="GLM-5",
        ADMIN_PASSWORD="secret",
    )

    page_data = build_config_page_data(
        settings_obj=settings_stub,
        env_path=env_path,
        env_example_path=example_path,
    )

    assert page_data["overview"]["total_sections"] >= 7
    assert page_data["overview"]["total_fields"] >= 35
    assert page_data["overview"]["overridden_fields"] == 2
    assert page_data["overview"]["example_exists"] is True

    field_map = {
        field["key"]: field
        for section in page_data["sections"]
        for field in section["fields"]
    }

    assert field_map["API_ENDPOINT"]["source_label"] == ".env"
    assert field_map["DEBUG_LOGGING"]["source_label"] == ".env"
    assert field_map["GLM5_MODEL"]["source_label"] == "默认值"
    assert field_map["ADMIN_PASSWORD"]["sensitive"] is True


@pytest.mark.asyncio
async def test_save_form_config_preserves_unmanaged_lines_and_updates_fields(
    tmp_path,
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CUSTOM_FLAG=keep\nSERVICE_NAME=old-service\n",
        encoding="utf-8",
    )

    reloaded = False

    async def fake_reload():
        nonlocal reloaded
        reloaded = True

    payload = _build_form_payload(
        SERVICE_NAME="new-service",
        LISTEN_PORT=9090,
        ROOT_PATH="/edge",
        DEBUG_LOGGING=False,
        TOKEN_AUTO_IMPORT_ENABLED=True,
        TOKEN_AUTO_IMPORT_SOURCE_DIR="/srv/tokens",
        HTTP_PROXY="http://127.0.0.1:7890",
        ADMIN_PASSWORD="new-admin-password",
    )

    updates = await save_form_config(
        payload,
        reload_callback=fake_reload,
        env_path=env_path,
    )
    content = env_path.read_text(encoding="utf-8")

    assert reloaded is True
    assert updates["SERVICE_NAME"] == "new-service"
    assert updates["LISTEN_PORT"] == 9090
    assert updates["TOKEN_AUTO_IMPORT_ENABLED"] is True
    assert "CUSTOM_FLAG=keep" in content
    assert "SERVICE_NAME=new-service" in content
    assert "LISTEN_PORT=9090" in content
    assert "ROOT_PATH=/edge" in content
    assert "TOKEN_AUTO_IMPORT_ENABLED=true" in content
    assert "TOKEN_AUTO_IMPORT_SOURCE_DIR=/srv/tokens" in content
    assert "HTTP_PROXY=http://127.0.0.1:7890" in content


@pytest.mark.asyncio
async def test_save_source_config_rolls_back_file_when_reload_fails(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SERVICE_NAME=old-service\n", encoding="utf-8")

    async def failing_reload():
        raise RuntimeError("reload failed")

    with pytest.raises(RuntimeError, match="reload failed"):
        await save_source_config(
            "SERVICE_NAME=new-service\nLISTEN_PORT=8081\n",
            reload_callback=failing_reload,
            env_path=env_path,
        )

    assert env_path.read_text(encoding="utf-8") == "SERVICE_NAME=old-service\n"


@pytest.mark.asyncio
async def test_save_config_endpoint_returns_refresh_trigger(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SERVICE_NAME=before\n", encoding="utf-8")

    async def fake_reload():
        return None

    monkeypatch.setattr(admin_api, "reload_settings", fake_reload)

    request = _make_form_request(
        "/admin/api/config/save",
        _build_form_payload(
            SERVICE_NAME="after",
            LISTEN_PORT=8081,
            DEBUG_LOGGING=True,
        ),
    )
    response = await admin_api.save_config(request)
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "admin-config-refresh"
    assert "保存成功" in body
    assert "SERVICE_NAME=after" in (tmp_path / ".env").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_save_config_source_endpoint_rejects_invalid_source(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SERVICE_NAME=before\n", encoding="utf-8")

    async def fake_reload():
        return None

    monkeypatch.setattr(admin_api, "reload_settings", fake_reload)

    request = _make_form_request(
        "/admin/api/config/source",
        {"env_content": "SERVICE_NAME=after\nnot-valid-line\n"},
    )
    response = await admin_api.save_config_source(request)
    body = response.body.decode("utf-8")

    assert response.status_code == 400
    assert "KEY=VALUE" in body
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SERVICE_NAME=before\n"


def test_validate_env_source_rejects_invalid_lines():
    with pytest.raises(ValueError, match="KEY=VALUE"):
        validate_env_source("SERVICE_NAME=ok\nbad line\n")


def test_config_template_compiles():
    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("config.html")

    assert template is not None
