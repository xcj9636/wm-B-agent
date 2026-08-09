import pytest

from app.integrations.hunter import (
    EmailVerificationResult,
    HunterConnectorError,
    get_hunter_client,
)
from app.main import app
from app.models.database import ContactVerification, Customer, OutboxEvent
from app.services.outreach_queue import (
    ContactSuppressed,
    OutreachQueueService,
    QueueOutreachCommand,
)


class ValidHunterClient:
    async def verify_email(self, email: str):
        assert email == "buyer@example.com"
        return EmailVerificationResult(
            status="valid",
            score=94,
            retryable=False,
            details={"smtp_check": True, "sources": ["https://example.com/public-page"]},
        )


class LegallyRestrictedHunterClient:
    async def verify_email(self, email: str):
        raise HunterConnectorError(
            error_code="legal_restriction",
            retryable=False,
            legal_restriction=True,
        )


def create_customer(db):
    customer = Customer(
        username="buyer",
        platform="email",
        email="buyer@example.com",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def test_customer_email_verification_is_auditable_and_secret_free(api_context):
    client, db, _ = api_context
    customer = create_customer(db)
    app.dependency_overrides[get_hunter_client] = lambda: ValidHunterClient()

    response = client.post(f"/api/v1/customers/{customer.id}/email-verification")

    assert response.status_code == 200
    assert response.json()["status"] == "valid"
    assert response.json()["score"] == 94
    assert "sources" not in response.text
    verification = db.query(ContactVerification).one()
    assert verification.customer_id == customer.id
    assert verification.email == "buyer@example.com"
    assert verification.status == "valid"
    assert verification.provider == "hunter"
    assert verification.legal_restricted is False


def test_hunter_451_suppresses_contact_and_outreach_queue_fails_closed(api_context):
    client, db, _ = api_context
    customer = create_customer(db)
    app.dependency_overrides[get_hunter_client] = lambda: LegallyRestrictedHunterClient()

    response = client.post(f"/api/v1/customers/{customer.id}/email-verification")

    assert response.status_code == 451
    db.refresh(customer)
    assert customer.custom_fields["contact_suppressed"] is True
    assert customer.custom_fields["suppression_reason"] == "legal_restriction"
    verification = db.query(ContactVerification).one()
    assert verification.legal_restricted is True

    with pytest.raises(ContactSuppressed):
        OutreachQueueService(db).queue(
            QueueOutreachCommand(
                customer_id=customer.id,
                channel="email",
                recipient=customer.email,
                subject="Hello",
                body="Introduction",
                business_key="suppressed-contact",
            )
        )
    assert db.query(OutboxEvent).count() == 0

