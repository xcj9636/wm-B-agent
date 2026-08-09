from app.api.v1.auth import get_current_active_user
from app.integrations.hunter import HunterConnectorError, get_hunter_client
from app.main import app
from app.models.database import (
    Customer,
    ProspectingContact,
    ProspectingSearch,
    User,
)


class FakeHunterSearchClient:
    def __init__(self):
        self.domain_calls = []
        self.finder_calls = []

    async def domain_search(self, **kwargs):
        self.domain_calls.append(kwargs)
        return {
            "domain": "acme.com",
            "organization": "Acme Trading",
            "emails": [
                {
                    "value": " Buyer@Acme.com ",
                    "type": "personal",
                    "confidence": 95,
                    "first_name": "Ada",
                    "last_name": "Buyer",
                    "position": "VP Sales",
                    "seniority": "executive",
                    "department": "sales",
                    "decision_maker": True,
                    "linkedin": "must-not-be-stored",
                    "verification": {"status": "valid", "date": "2026-08-01"},
                    "sources": [
                        {
                            "domain": "acme.com",
                            "uri": "https://acme.com/team/ada",
                            "extracted_on": "2026-01-02",
                            "last_seen_on": "2026-08-01",
                        },
                        {"domain": "bad", "uri": "javascript:alert(1)"},
                    ],
                },
                {
                    "value": "buyer@acme.com",
                    "first_name": "Duplicate",
                    "verification": {"status": "valid"},
                },
                {
                    "value": "ops@acme.com",
                    "type": "generic",
                    "confidence": 72,
                    "position": "Operations",
                    "department": "operations",
                    "decision_maker": False,
                    "verification": {"status": "accept_all"},
                    "sources": [],
                },
            ],
        }

    async def email_finder(self, **kwargs):
        self.finder_calls.append(kwargs)
        return {
            "email": "founder@acme.com",
            "first_name": "Lin",
            "last_name": "Founder",
            "company": "Acme Trading",
            "domain": "acme.com",
            "position": "Founder",
            "score": 97,
            "verification": {"status": "valid", "date": "2026-08-02"},
            "sources": [],
            "linkedin_url": "must-not-be-stored",
        }


class LegallyRestrictedFinder:
    async def email_finder(self, **kwargs):
        raise HunterConnectorError(
            error_code="legal_restriction",
            retryable=False,
            legal_restriction=True,
        )


def domain_search_payload():
    return {
        "mode": "domain_search",
        "domain": "https://ACME.com/about",
        "limit": 25,
        "contact_type": "personal",
        "seniorities": ["executive"],
        "departments": ["sales"],
        "decision_maker": True,
        "verification_statuses": ["valid"],
    }


def test_domain_search_is_persisted_deduplicated_and_source_safe(api_context):
    client, db, _ = api_context
    hunter = FakeHunterSearchClient()
    app.dependency_overrides[get_hunter_client] = lambda: hunter

    response = client.post("/api/v1/prospecting/searches", json=domain_search_payload())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["mode"] == "domain_search"
    assert body["query"]["domain"] == "acme.com"
    assert body["result_count"] == 2
    assert len(body["contacts"]) == 2
    assert body["contacts"][0]["email"] == "buyer@acme.com"
    assert body["contacts"][0]["evidence"] == [
        {
            "domain": "acme.com",
            "uri": "https://acme.com/team/ada",
            "extracted_on": "2026-01-02",
            "last_seen_on": "2026-08-01",
        }
    ]
    assert "linkedin" not in response.text.lower()
    assert hunter.domain_calls == [
        {
            "domain": "acme.com",
            "company": None,
            "limit": 25,
            "offset": 0,
            "contact_type": "personal",
            "seniorities": ["executive"],
            "departments": ["sales"],
            "decision_maker": True,
            "verification_statuses": ["valid"],
        }
    ]
    assert db.query(ProspectingSearch).count() == 1
    assert db.query(ProspectingContact).count() == 2


def test_email_finder_result_can_be_imported_and_email_deduped(api_context):
    client, db, _ = api_context
    hunter = FakeHunterSearchClient()
    app.dependency_overrides[get_hunter_client] = lambda: hunter
    search = client.post(
        "/api/v1/prospecting/searches",
        json={
            "mode": "email_finder",
            "company": "Acme Trading",
            "first_name": "Lin",
            "last_name": "Founder",
            "max_duration": 12,
        },
    ).json()
    contact_id = search["contacts"][0]["id"]

    first = client.post(
        "/api/v1/prospecting/contacts/import",
        json={"contact_ids": [contact_id]},
    )
    second = client.post(
        "/api/v1/prospecting/contacts/import",
        json={"contact_ids": [contact_id]},
    )

    assert first.status_code == 200
    assert first.json() == {"created": 1, "existing": 0, "customer_ids": [1]}
    assert second.json() == {"created": 0, "existing": 1, "customer_ids": [1]}
    customer = db.query(Customer).one()
    assert customer.email == "founder@acme.com"
    assert customer.company_name == "Acme Trading"
    assert customer.job_title == "Founder"
    assert customer.source_data_json["provider"] == "hunter"
    assert "linkedin" not in str(customer.source_data_json).lower()
    assert hunter.finder_calls[0]["max_duration"] == 12


def test_accept_all_import_is_suppressed_before_any_outreach(api_context):
    client, db, _ = api_context
    app.dependency_overrides[get_hunter_client] = lambda: FakeHunterSearchClient()
    search = client.post("/api/v1/prospecting/searches", json=domain_search_payload()).json()
    accept_all = next(
        contact for contact in search["contacts"] if contact["verification_status"] == "accept_all"
    )

    response = client.post(
        "/api/v1/prospecting/contacts/import",
        json={"contact_ids": [accept_all["id"]]},
    )

    assert response.status_code == 200
    customer = db.query(Customer).one()
    assert customer.custom_fields["contact_suppressed"] is True
    assert customer.custom_fields["suppression_reason"] == "email_accept_all"


def test_legal_restriction_is_recorded_without_persisting_contact_pii(api_context):
    client, db, _ = api_context
    app.dependency_overrides[get_hunter_client] = lambda: LegallyRestrictedFinder()

    response = client.post(
        "/api/v1/prospecting/searches",
        json={
            "mode": "email_finder",
            "domain": "acme.com",
            "full_name": "Restricted Person",
        },
    )

    assert response.status_code == 451
    search = db.query(ProspectingSearch).one()
    assert search.status == "failed"
    assert search.error_code == "legal_restriction"
    assert db.query(ProspectingContact).count() == 0
    assert "Restricted Person" not in str(search.query_json)


def test_users_cannot_import_another_users_search_contacts(api_context):
    client, db, _ = api_context
    app.dependency_overrides[get_hunter_client] = lambda: FakeHunterSearchClient()
    search = client.post("/api/v1/prospecting/searches", json=domain_search_payload()).json()
    contact_id = search["contacts"][0]["id"]
    other = User(
        username="other-user",
        email="other@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db.add(other)
    db.commit()
    app.dependency_overrides[get_current_active_user] = lambda: other

    response = client.post(
        "/api/v1/prospecting/contacts/import",
        json={"contact_ids": [contact_id]},
    )

    assert response.status_code == 404
    assert db.query(Customer).count() == 0
