import stat
from pathlib import Path

from app.config import settings
from app.models.database import ConnectorConfiguration


def test_connector_catalog_and_inventory_require_administrator(api_context):
    client, _, _ = api_context

    assert client.get("/api/v1/connectors/catalog").status_code == 403
    assert client.get("/api/v1/connectors").status_code == 403


def test_admin_can_hot_configure_hunter_without_secret_exposure(
    api_context,
    tmp_path,
    monkeypatch,
):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    monkeypatch.setattr(settings, "CONNECTOR_SECRET_DIR", str(tmp_path))

    catalog = client.get("/api/v1/connectors/catalog")
    assert catalog.status_code == 200
    assert catalog.json()[0]["provider"] == "hunter"

    created = client.post(
        "/api/v1/connectors",
        json={
            "provider": "hunter",
            "name": "Hunter production",
            "secret": "hunter-secret",
            "config": {"timeout_seconds": 12},
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["provider"] == "hunter"
    assert body["enabled"] is False
    assert body["version"] == 1
    assert body["secret_configured"] is True
    assert "hunter-secret" not in created.text
    assert "secret_ref" not in created.text

    stored = db.query(ConnectorConfiguration).one()
    secret_file = Path(stored.secret_ref)
    assert secret_file.read_text() == "hunter-secret"
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600

    listed = client.get("/api/v1/connectors")
    assert listed.status_code == 200
    assert listed.json() == [body]
    assert "hunter-secret" not in listed.text


def test_connector_update_is_versioned_and_secret_is_write_only(
    api_context,
    tmp_path,
    monkeypatch,
):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    monkeypatch.setattr(settings, "CONNECTOR_SECRET_DIR", str(tmp_path))

    created = client.post(
        "/api/v1/connectors",
        json={"provider": "hunter", "name": "Primary", "secret": "first-secret"},
    ).json()
    updated = client.put(
        f"/api/v1/connectors/{created['id']}",
        json={"name": "Primary Hunter", "secret": "second-secret"},
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "Primary Hunter"
    assert updated.json()["version"] == 2
    assert "second-secret" not in updated.text
    stored = db.query(ConnectorConfiguration).one()
    assert Path(stored.secret_ref).read_text() == "second-secret"


def test_connector_cannot_be_enabled_before_a_healthy_probe(
    api_context,
    tmp_path,
    monkeypatch,
):
    client, db, user = api_context
    user.is_superuser = True
    db.commit()
    monkeypatch.setattr(settings, "CONNECTOR_SECRET_DIR", str(tmp_path))
    connector = client.post(
        "/api/v1/connectors",
        json={"provider": "hunter", "name": "Primary", "secret": "secret"},
    ).json()

    response = client.post(f"/api/v1/connectors/{connector['id']}/enable")

    assert response.status_code == 409
    assert "connection test" in response.json()["detail"].lower()
