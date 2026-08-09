def workflow_payload(name="Prospect qualification"):
    return {
        "name": name,
        "description": "Qualify a prospect",
        "steps": [
            {
                "name": "scrape",
                "skill_name": "social_scraper",
                "condition": "always",
            }
        ],
        "transitions": [],
        "variables": {"region": "global"},
        "tags": ["sales"],
    }


def test_create_workflow_persists_a_serializable_definition(api_context):
    client, _, _ = api_context

    response = client.post("/api/v1/workflows", json=workflow_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["config_json"]["steps"][0]["condition"] == "always"
    assert body["config_json"]["variables"] == {"region": "global"}


def test_update_workflow_rebuilds_definition_for_metadata_only_change(api_context):
    client, _, _ = api_context
    created = client.post("/api/v1/workflows", json=workflow_payload()).json()

    response = client.put(
        f"/api/v1/workflows/{created['id']}",
        json={"name": "Renamed qualification"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Renamed qualification"
    assert body["config_json"]["name"] == "Renamed qualification"


def test_update_workflow_accepts_replacement_steps(api_context):
    client, _, _ = api_context
    created = client.post("/api/v1/workflows", json=workflow_payload()).json()

    response = client.put(
        f"/api/v1/workflows/{created['id']}",
        json={
            "steps": [
                {
                    "name": "score",
                    "skill_name": "lead_scoring",
                    "condition": "on_success",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    step = response.json()["config_json"]["steps"][0]
    assert step["skill_name"] == "lead_scoring"
    assert step["condition"] == "on_success"
