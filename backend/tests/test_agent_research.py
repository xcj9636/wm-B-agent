import json
from datetime import datetime, timezone

from app.main import app
from app.models.database import Customer, User
from app.services.ai_runtime import get_ai_runtime_service
from app.services.llm.contracts import LLMResponse, LLMUsage


class FakeDraftBackend:
    def __init__(self):
        self.requests = []
        self.closed = False

    async def complete(self, request):
        self.requests.append(request)
        evidence = json.loads(request.messages[-1].content)["evidence"]
        return LLMResponse(
            request_id=request.request_id,
            content=json.dumps(
                {
                    "subject": "German distribution fit for Acme",
                    "body": (
                        "Hi Ada, I noticed Acme opened a German sales office. "
                        "Would a short distributor-fit conversation be useful?"
                    ),
                    "personalization_points": [
                        "Opened a German sales office",
                    ],
                    "evidence_ids": [evidence[0]["id"], evidence[-1]["id"]],
                }
            ),
            resolved_model="draft-v1",
            resolved_provider="approved-provider",
            gateway_request_id="gw-draft-1",
            usage=LLMUsage(input_tokens=90, output_tokens=35),
        )

    async def aclose(self):
        self.closed = True


class FakeRuntime:
    def __init__(self, backend):
        self.backend = backend

    def build_backend(self):
        return self.backend


def seed_customer(db, *, suppressed=False, stale_icp=False):
    suffix = db.query(Customer).count() + 1
    customer = Customer(
        username=f"ada.buyer.{suffix}",
        platform="hunter",
        email="ada@acme.example",
        company_name="Acme Distribution",
        website="https://acme.example",
        job_title="VP Sales",
        country="DE",
        status="new",
        contact_info={"first_name": "Ada", "last_name": "Buyer"},
        custom_fields={
            "email_verification_status": "valid",
            "contact_suppressed": suppressed,
            "icp_recommended": not stale_icp,
            "icp_review_status": "qualified",
            "icp_score": 88.0,
            "icp_tier": "A",
        },
        source_data_json={
            "provider": "hunter",
            "icp": {
                "stale": stale_icp,
                "profile_version": 2,
                "reasons": ["department_match", "verified_email"],
                "missing_signals": ["company_size"],
            },
        },
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def research_payload(customer_id):
    return {
        "customer_id": customer_id,
        "objective": "Validate distributor fit in Germany",
    }


def evidence_payload():
    observed_at = datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()
    return {
        "profile_evidence": [
            {
                "field": "industry",
                "value": "Industrial automation distribution",
                "source_url": "https://acme.example/about",
                "observed_at": observed_at,
                "confidence": 0.95,
            },
            {
                "field": "company_size",
                "value": "51-200 employees",
                "source_url": "https://acme.example/company",
                "observed_at": observed_at,
                "confidence": 0.8,
            },
        ],
        "market_signals": [
            {
                "type": "market_expansion",
                "summary": "Opened a German sales office",
                "source_url": "https://acme.example/news/germany-office",
                "observed_at": observed_at,
                "confidence": 0.9,
            }
        ],
    }


def create_approved_job(client, customer_id):
    created = client.post(
        "/api/v1/agent/research-jobs",
        json=research_payload(customer_id),
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]
    enriched = client.put(
        f"/api/v1/agent/research-jobs/{job_id}/evidence",
        json=evidence_payload(),
    )
    assert enriched.status_code == 200, enriched.text
    reviewed = client.post(
        f"/api/v1/agent/research-jobs/{job_id}/review",
        json={"decision": "approve", "reason": "Sources checked"},
    )
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def test_research_queue_captures_sourced_firmographics_and_signals(api_context):
    client, db, _ = api_context
    customer = seed_customer(db)

    created = client.post(
        "/api/v1/agent/research-jobs",
        json=research_payload(customer.id),
    )

    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "queued"
    assert job["company_name"] == "Acme Distribution"
    assert job["version"] == 1
    assert "industry" in job["missing_fields"]
    assert "market_signals" in job["missing_fields"]

    enriched = client.put(
        f"/api/v1/agent/research-jobs/{job['id']}/evidence",
        json=evidence_payload(),
    )

    assert enriched.status_code == 200, enriched.text
    body = enriched.json()
    assert body["status"] == "in_review"
    assert body["version"] == 2
    assert len(body["profile_evidence"]) == 2
    assert len(body["market_signals"]) == 1
    assert body["profile_evidence"][0]["id"]
    assert body["market_signals"][0]["source_url"].startswith("https://")
    assert "industry" not in body["missing_fields"]
    assert "market_signals" not in body["missing_fields"]

    approved = client.post(
        f"/api/v1/agent/research-jobs/{job['id']}/review",
        json={"decision": "approve", "reason": "Sources checked"},
    )
    listed = client.get("/api/v1/agent/research-jobs")

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert approved.json()["review_reason"] == "Sources checked"
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [job["id"]]


def test_research_evidence_requires_web_sources_and_reviewable_content(api_context):
    client, db, _ = api_context
    customer = seed_customer(db)
    job_id = client.post(
        "/api/v1/agent/research-jobs",
        json=research_payload(customer.id),
    ).json()["id"]

    invalid = evidence_payload()
    invalid["profile_evidence"][0]["source_url"] = "file:///etc/passwd"
    rejected = client.put(
        f"/api/v1/agent/research-jobs/{job_id}/evidence",
        json=invalid,
    )
    empty_review = client.post(
        f"/api/v1/agent/research-jobs/{job_id}/review",
        json={"decision": "approve", "reason": "No evidence"},
    )

    assert rejected.status_code == 422
    assert empty_review.status_code == 409

    credentialed = evidence_payload()
    credentialed["profile_evidence"][0]["source_url"] = (
        "https://token:secret@acme.example/private-report"
    )
    leaked_credentials = client.put(
        f"/api/v1/agent/research-jobs/{job_id}/evidence",
        json=credentialed,
    )
    assert leaked_credentials.status_code == 422


def test_approved_research_generates_idempotent_evidence_bound_draft(api_context):
    client, db, _ = api_context
    customer = seed_customer(db)
    job = create_approved_job(client, customer.id)
    backend = FakeDraftBackend()
    app.dependency_overrides[get_ai_runtime_service] = lambda: FakeRuntime(backend)
    command = {
        "channel": "email",
        "language": "en",
        "goal": "Request a 20-minute distributor-fit call",
        "idempotency_key": "research-acme-email-v1",
    }

    generated = client.post(
        f"/api/v1/agent/research-jobs/{job['id']}/drafts",
        json=command,
    )
    repeated = client.post(
        f"/api/v1/agent/research-jobs/{job['id']}/drafts",
        json=command,
    )

    assert generated.status_code == 201, generated.text
    draft = generated.json()
    assert draft["status"] == "draft"
    assert draft["channel"] == "email"
    assert draft["subject"] == "German distribution fit for Acme"
    assert draft["resolved_provider"] == "approved-provider"
    assert draft["resolved_model"] == "draft-v1"
    assert draft["research_version"] == job["version"]
    assert draft["stale"] is False
    assert len(draft["evidence_ids"]) == 2
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == draft["id"]
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.use_case.value == "message_draft"
    assert request.response_schema is not None
    prompt = request.messages[-1].content
    assert "department_match" in prompt
    assert "Opened a German sales office" in prompt
    assert "missing_signals" in prompt
    assert backend.closed is True

    customer.job_title = "Chief Procurement Officer"
    db.commit()
    changed_context = client.post(
        f"/api/v1/agent/research-jobs/{job['id']}/drafts",
        json=command,
    )
    assert changed_context.status_code == 409


def test_draft_generation_and_approval_fail_closed_on_stale_or_suppressed_context(
    api_context,
):
    client, db, _ = api_context
    safe_customer = seed_customer(db)
    job = create_approved_job(client, safe_customer.id)
    backend = FakeDraftBackend()
    app.dependency_overrides[get_ai_runtime_service] = lambda: FakeRuntime(backend)
    command = {
        "channel": "email",
        "language": "en",
        "goal": "Request distributor call",
        "idempotency_key": "draft-stale-check",
    }
    generated = client.post(
        f"/api/v1/agent/research-jobs/{job['id']}/drafts",
        json=command,
    )
    assert generated.status_code == 201, generated.text

    revised = evidence_payload()
    revised["market_signals"][0]["summary"] = "Expanded the German office"
    updated = client.put(
        f"/api/v1/agent/research-jobs/{job['id']}/evidence",
        json=revised,
    )
    stale_approval = client.patch(
        f"/api/v1/agent/outreach-drafts/{generated.json()['id']}/review",
        json={"decision": "approve", "reason": "Ready to use"},
    )
    detail = client.get(f"/api/v1/agent/research-jobs/{job['id']}")

    assert updated.status_code == 200
    assert updated.json()["status"] == "in_review"
    assert detail.json()["drafts"][0]["stale"] is True
    assert stale_approval.status_code == 409

    suppressed = seed_customer(db, suppressed=True)
    suppressed_job = create_approved_job(client, suppressed.id)
    blocked = client.post(
        f"/api/v1/agent/research-jobs/{suppressed_job['id']}/drafts",
        json={**command, "idempotency_key": "suppressed-contact"},
    )
    assert blocked.status_code == 409

    stale_customer = seed_customer(db, stale_icp=True)
    stale_job = create_approved_job(client, stale_customer.id)
    stale = client.post(
        f"/api/v1/agent/research-jobs/{stale_job['id']}/drafts",
        json={**command, "idempotency_key": "stale-icp"},
    )
    assert stale.status_code == 409


def test_research_queue_and_drafts_are_user_owned(api_context):
    client, db, _ = api_context
    customer = seed_customer(db)
    job = create_approved_job(client, customer.id)
    other = User(
        username="research-other",
        email="research-other@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db.add(other)
    db.commit()
    from app.api.v1.auth import get_current_active_user

    app.dependency_overrides[get_current_active_user] = lambda: other

    assert client.get(
        f"/api/v1/agent/research-jobs/{job['id']}"
    ).status_code == 404
    assert client.put(
        f"/api/v1/agent/research-jobs/{job['id']}/evidence",
        json=evidence_payload(),
    ).status_code == 404
