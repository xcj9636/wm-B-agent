from datetime import datetime, timedelta

from app.models.database import OutboxEvent, OutboxStatus


def add_dead_letter(
    db,
    *,
    key,
    channel,
    error_code,
    created_at,
):
    event = OutboxEvent(
        aggregate_type="outreach_log",
        aggregate_id=f"aggregate-{key}",
        event_type="send",
        business_key=f"private-business-{key}",
        channel=channel,
        payload_json={
            "to": f"private-{key}@example.com",
            "body": f"secret message {key}",
        },
        payload_hash="a" * 64,
        status=OutboxStatus.DEAD_LETTER,
        available_at=created_at,
        attempt_count=2,
        max_attempts=5,
        last_error=error_code,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(event)
    db.commit()
    return event


def test_dead_letter_list_requires_superuser(api_context):
    client, _, _ = api_context

    response = client.get("/api/v1/admin/reliable-execution/dead-letters")

    assert response.status_code == 403


def test_superuser_lists_secret_free_dead_letters_with_filters(api_context):
    client, db, user = api_context
    user.is_superuser = True
    now = datetime.utcnow()
    newest = add_dead_letter(
        db,
        key="email-new",
        channel="email",
        error_code="lease_expired_unknown_delivery_state",
        created_at=now,
    )
    add_dead_letter(
        db,
        key="email-old",
        channel="email",
        error_code="raw provider stack includes secret-token",
        created_at=now - timedelta(minutes=2),
    )
    add_dead_letter(
        db,
        key="whatsapp",
        channel="whatsapp",
        error_code="provider_response_lost",
        created_at=now - timedelta(minutes=1),
    )

    response = client.get(
        "/api/v1/admin/reliable-execution/dead-letters",
        params={"channel": "email", "limit": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert data[0] == {
        "id": str(newest.id),
        "aggregate_type": "outreach_log",
        "aggregate_id": "aggregate-email-new",
        "event_type": "send",
        "channel": "email",
        "attempt_count": 2,
        "max_attempts": 5,
        "error_code": "lease_expired_unknown_delivery_state",
        "created_at": newest.created_at.isoformat(),
        "updated_at": newest.updated_at.isoformat(),
    }
    assert len(data) == 1
    assert "private-email-new@example.com" not in response.text
    assert "secret message" not in response.text
    assert "private-business" not in response.text
    assert "secret-token" not in response.text


def test_dead_letter_list_sanitizes_legacy_error_text(api_context):
    client, db, user = api_context
    user.is_superuser = True
    add_dead_letter(
        db,
        key="legacy-error",
        channel="email",
        error_code="SMTP failed for private@example.com password=hunter2",
        created_at=datetime.utcnow(),
    )

    response = client.get("/api/v1/admin/reliable-execution/dead-letters")

    assert response.status_code == 200
    assert response.json()[0]["error_code"] == "delivery_failure"
    assert "private@example.com" not in response.text
    assert "hunter2" not in response.text

