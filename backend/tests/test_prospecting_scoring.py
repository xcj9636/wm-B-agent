from app.api.v1.auth import get_current_active_user
from app.main import app
from app.models.database import Customer, ProspectingContact, ProspectingSearch, User


def seed_rankable_search(db, user):
    search = ProspectingSearch(
        user_id=user.id,
        provider="hunter",
        mode="domain_search",
        query_json={"domain": "acme.com"},
        status="completed",
        connector_version=7,
        result_count=2,
    )
    db.add(search)
    db.flush()
    db.add_all(
        [
            ProspectingContact(
                search_id=search.id,
                email="vp@acme.com",
                first_name="Ada",
                last_name="Buyer",
                company="Acme",
                domain="acme.com",
                position="VP Sales",
                department="sales",
                seniority="executive",
                contact_type="personal",
                confidence=96,
                decision_maker=True,
                verification_status="valid",
                evidence_json=[
                    {"domain": "acme.com", "uri": "https://acme.com/team"},
                    {"domain": "acme.com", "uri": "https://acme.com/about"},
                ],
            ),
            ProspectingContact(
                search_id=search.id,
                email="hello@acme.com",
                company="Acme",
                domain="acme.com",
                position="Operations Assistant",
                department="operations",
                seniority="junior",
                contact_type="generic",
                confidence=35,
                decision_maker=False,
                verification_status="unknown",
                evidence_json=[],
            ),
        ]
    )
    db.commit()
    db.refresh(search)
    return search


def profile_payload(**overrides):
    values = {
        "name": "European distributor decision makers",
        "target_departments": ["sales", "management"],
        "target_seniorities": ["executive", "senior"],
        "title_keywords": ["vp", "director", "head"],
        "preferred_contact_types": ["personal"],
        "weights": {
            "role_fit": 40,
            "contact_quality": 35,
            "evidence_quality": 25,
        },
        "minimum_score": 65,
    }
    values.update(overrides)
    return values


def test_icp_profile_is_versioned_and_rejects_invalid_weights(api_context):
    client, _, _ = api_context

    default = client.get("/api/v1/prospecting/icp-profile")
    updated = client.put(
        "/api/v1/prospecting/icp-profile",
        json=profile_payload(),
    )
    invalid = client.put(
        "/api/v1/prospecting/icp-profile",
        json=profile_payload(
            weights={
                "role_fit": 60,
                "contact_quality": 35,
                "evidence_quality": 25,
            }
        ),
    )

    assert default.status_code == 200, default.text
    assert default.json()["version"] == 1
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["title_keywords"] == ["vp", "director", "head"]
    assert invalid.status_code == 422


def test_search_scoring_is_explainable_ranked_and_read_only(api_context):
    client, db, user = api_context
    search = seed_rankable_search(db, user)
    client.put("/api/v1/prospecting/icp-profile", json=profile_payload())

    response = client.post(f"/api/v1/prospecting/searches/{search.id}/score")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["search_id"] == str(search.id)
    assert body["profile_version"] == 2
    assert [row["email"] for row in body["scores"]] == [
        "vp@acme.com",
        "hello@acme.com",
    ]
    best, lowest = body["scores"]
    assert best["final_score"] > lowest["final_score"]
    assert best["tier"] == "A"
    assert best["recommended"] is True
    assert set(best["factor_scores"]) == {
        "role_fit",
        "contact_quality",
        "evidence_quality",
    }
    assert best["reasons"]
    assert lowest["recommended"] is False
    assert "evidence" in lowest["missing_signals"]
    assert db.query(ProspectingContact).count() == 2


def test_manual_review_adjustment_survives_rescoring(api_context):
    client, db, user = api_context
    search = seed_rankable_search(db, user)
    client.put("/api/v1/prospecting/icp-profile", json=profile_payload())
    ranking = client.post(
        f"/api/v1/prospecting/searches/{search.id}/score"
    ).json()
    low = ranking["scores"][1]

    reviewed = client.patch(
        f"/api/v1/prospecting/scores/{low['id']}/review",
        json={
            "review_status": "qualified",
            "score_adjustment": 15,
            "review_reason": "Known regional buying committee member",
        },
    )
    rescored = client.post(
        f"/api/v1/prospecting/searches/{search.id}/score"
    )

    assert reviewed.status_code == 200, reviewed.text
    reviewed_body = reviewed.json()
    assert reviewed_body["review_status"] == "qualified"
    assert reviewed_body["final_score"] == min(
        reviewed_body["base_score"] + 15,
        100,
    )
    refreshed_low = next(
        row for row in rescored.json()["scores"] if row["id"] == low["id"]
    )
    assert refreshed_low["score_adjustment"] == 15
    assert refreshed_low["review_reason"] == (
        "Known regional buying committee member"
    )


def test_ranking_is_marked_stale_after_icp_policy_changes(api_context):
    client, db, user = api_context
    search = seed_rankable_search(db, user)
    client.put("/api/v1/prospecting/icp-profile", json=profile_payload())
    scored = client.post(
        f"/api/v1/prospecting/searches/{search.id}/score"
    ).json()

    client.put(
        "/api/v1/prospecting/icp-profile",
        json=profile_payload(name="Updated buying committee ICP"),
    )
    ranking = client.get(
        f"/api/v1/prospecting/searches/{search.id}/ranking"
    )

    assert ranking.status_code == 200, ranking.text
    assert ranking.json()["stale"] is True
    assert ranking.json()["profile_version"] == 3
    assert ranking.json()["scores"][0]["profile_version"] == (
        scored["profile_version"]
    )
    assert ranking.json()["scores"][0]["stale"] is True
    assert ranking.json()["scores"][0]["recommended"] is False

    imported = client.post(
        "/api/v1/prospecting/contacts/import",
        json={"contact_ids": [ranking.json()["scores"][0]["contact_id"]]},
    )

    assert imported.status_code == 200, imported.text
    customer = db.query(Customer).one()
    assert customer.custom_fields["icp_recommended"] is False
    assert customer.source_data_json["icp"]["stale"] is True


def test_other_user_cannot_score_or_read_search_ranking(api_context):
    client, db, user = api_context
    search = seed_rankable_search(db, user)
    other = User(
        username="scoring-other",
        email="scoring-other@example.com",
        hashed_password="unused",
        is_active=True,
    )
    db.add(other)
    db.commit()
    app.dependency_overrides[get_current_active_user] = lambda: other

    assert client.post(
        f"/api/v1/prospecting/searches/{search.id}/score"
    ).status_code == 404
    assert client.get(
        f"/api/v1/prospecting/searches/{search.id}/ranking"
    ).status_code == 404


def test_approved_import_carries_icp_evidence_into_customer_context(api_context):
    client, db, user = api_context
    search = seed_rankable_search(db, user)
    client.put("/api/v1/prospecting/icp-profile", json=profile_payload())
    ranking = client.post(
        f"/api/v1/prospecting/searches/{search.id}/score"
    ).json()
    best = ranking["scores"][0]
    client.patch(
        f"/api/v1/prospecting/scores/{best['id']}/review",
        json={
            "review_status": "qualified",
            "score_adjustment": 3,
            "review_reason": "Confirmed purchasing authority",
        },
    )

    response = client.post(
        "/api/v1/prospecting/contacts/import",
        json={"contact_ids": [best["contact_id"]]},
    )

    assert response.status_code == 200, response.text
    customer = db.query(Customer).one()
    assert customer.custom_fields["icp_tier"] == "A"
    assert customer.custom_fields["icp_recommended"] is True
    assert customer.custom_fields["icp_review_status"] == "qualified"
    icp_evidence = customer.source_data_json["icp"]
    assert icp_evidence["profile_version"] == 2
    assert set(icp_evidence["factor_scores"]) == {
        "role_fit",
        "contact_quality",
        "evidence_quality",
    }
    assert icp_evidence["reasons"]
