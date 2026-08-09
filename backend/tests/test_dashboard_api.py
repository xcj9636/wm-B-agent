from datetime import datetime, timedelta

from app.models.database import AuditLog, Customer, IntentLevel


def test_high_intent_endpoint_returns_only_prioritized_leads(api_context):
    client, db, _ = api_context
    db.add_all(
        [
            Customer(
                username="priority-brand",
                platform="instagram",
                company_name="Priority Brand",
                intent_level=IntentLevel.VERY_HIGH,
            ),
            Customer(
                username="not-ready",
                platform="tiktok",
                intent_level=IntentLevel.LOW,
            ),
        ]
    )
    db.commit()

    response = client.get("/api/v1/customers/high-intent")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "id": 1,
            "name": "Priority Brand",
            "intent": "very_high",
            "platform": "instagram",
        }
    ]


def test_activity_endpoint_exposes_recent_user_audit_events(api_context):
    client, db, user = api_context
    now = datetime.utcnow()
    db.add_all(
        [
            AuditLog(
                user_id=user.id,
                action="create",
                resource_type="customer",
                resource_id="42",
                details_json={"source": "instagram"},
                created_at=now,
            ),
            AuditLog(
                user_id=user.id + 1,
                action="execute",
                resource_type="workflow",
                resource_id="foreign",
                created_at=now + timedelta(seconds=1),
            ),
        ]
    )
    db.commit()

    response = client.get("/api/v1/stats/activities")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    activity = response.json()[0]
    assert activity["type"] == "customer_created"
    assert activity["description"] == "Customer created"
    assert activity["metadata"] == {"source": "instagram"}
