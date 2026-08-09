from app.api.v1.auth import create_refresh_token


def test_logout_acknowledges_authenticated_client(api_context):
    client, _, _ = api_context

    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Logged out"}


def test_refresh_accepts_the_frontend_json_contract(api_context):
    client, _, user = api_context
    refresh_token = create_refresh_token({"sub": str(user.id)})

    response = client.post(
        "/api/v1/auth/refresh",
        json={"token": refresh_token},
    )

    assert response.status_code == 200, response.text
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]
