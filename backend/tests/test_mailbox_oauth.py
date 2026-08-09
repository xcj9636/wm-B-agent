from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import stat
from urllib.parse import parse_qs, urlparse

from app.api.v1.mailboxes import get_mailbox_oauth_service
from app.config import settings
from app.main import app
from app.models.database import Account, MailboxOAuthSession
from app.services.mailbox_oauth import (
    MailboxIdentity,
    MailboxOAuthService,
    OAuthTokenSet,
)


class FakeOAuthProvider:
    def __init__(self, provider="gmail"):
        self.provider = provider
        self.exchanges = []

    def authorization_url(self, *, state, code_challenge):
        base = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            if self.provider == "gmail"
            else "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        )
        return (
            f"{base}?state={state}&code_challenge={code_challenge}"
            "&code_challenge_method=S256&access_type=offline"
        )

    async def exchange(self, *, code, code_verifier):
        self.exchanges.append((code, code_verifier))
        return OAuthTokenSet(
            access_token="access-secret",
            refresh_token="refresh-secret",
            expires_at=datetime.utcnow() + timedelta(hours=1),
            scopes=["mail.send", "mail.read"],
        )

    async def identity(self, *, access_token):
        assert access_token == "access-secret"
        return MailboxIdentity(
            subject="provider-user-7",
            email="sales@example.com",
            display_name="Export Sales",
        )


def oauth_service(db, tmp_path, provider):
    return MailboxOAuthService(
        db,
        config=settings,
        provider_builder=lambda name: provider,
        secret_root=tmp_path,
    )


def test_oauth_start_uses_one_time_hashed_state_and_pkce(api_context, tmp_path):
    client, db, _ = api_context
    provider = FakeOAuthProvider()
    service = oauth_service(db, tmp_path, provider)
    app.dependency_overrides[get_mailbox_oauth_service] = lambda: service

    started = client.post(
        "/api/v1/mailboxes/oauth/start",
        json={"provider": "gmail", "return_to": "/settings"},
    )

    assert started.status_code == 201, started.text
    body = started.json()
    query = parse_qs(urlparse(body["authorization_url"]).query)
    state = query["state"][0]
    challenge = query["code_challenge"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert len(state) >= 32
    assert len(challenge) >= 43
    assert "code_verifier" not in body
    assert state not in started.text.replace(body["authorization_url"], "")

    session = db.query(MailboxOAuthSession).one()
    assert session.state_hash == hashlib.sha256(state.encode()).hexdigest()
    assert state not in session.state_hash
    verifier_path = Path(session.code_verifier_ref)
    verifier = verifier_path.read_text()
    assert len(verifier) >= 43
    assert stat.S_IMODE(verifier_path.stat().st_mode) == 0o600


def test_oauth_callback_creates_secret_backed_account_and_rejects_replay(
    api_context,
    tmp_path,
):
    client, db, _ = api_context
    provider = FakeOAuthProvider()
    service = oauth_service(db, tmp_path, provider)
    app.dependency_overrides[get_mailbox_oauth_service] = lambda: service
    started = client.post(
        "/api/v1/mailboxes/oauth/start",
        json={"provider": "gmail", "return_to": "/settings"},
    ).json()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]

    completed = client.get(
        "/api/v1/mailboxes/oauth/callback/gmail",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )

    assert completed.status_code == 303, completed.text
    assert completed.headers["location"].startswith(
        "http://localhost:3000/settings?mailbox_oauth=success&provider=gmail"
    )
    assert "access-secret" not in completed.text
    assert "refresh-secret" not in completed.text
    account = db.query(Account).one()
    assert account.user_id is not None
    assert account.account_type == "gmail"
    assert account.email == "sales@example.com"
    assert account.oauth_subject == "provider-user-7"
    assert account.connection_status == "connected"
    assert account.is_verified is True
    assert account.credentials_json is None
    secret_path = Path(account.credential_secret_ref)
    secret = json.loads(secret_path.read_text())
    assert secret["access_token"] == "access-secret"
    assert secret["refresh_token"] == "refresh-secret"
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
    assert not Path(db.query(MailboxOAuthSession).one().code_verifier_ref).exists()

    replayed = client.get(
        "/api/v1/mailboxes/oauth/callback/gmail",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )
    assert replayed.status_code == 409
    assert len(provider.exchanges) == 1

    listed = client.get("/api/v1/mailboxes")
    assert listed.status_code == 200
    assert listed.json()[0]["secret_configured"] is True
    assert "access-secret" not in listed.text
    assert "refresh-secret" not in listed.text
    assert "credential_secret_ref" not in listed.text


def test_oauth_start_fails_closed_when_provider_is_not_configured(
    api_context,
    tmp_path,
    monkeypatch,
):
    client, db, _ = api_context
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "")
    service = MailboxOAuthService(db, config=settings, secret_root=tmp_path)
    app.dependency_overrides[get_mailbox_oauth_service] = lambda: service

    response = client.post(
        "/api/v1/mailboxes/oauth/start",
        json={"provider": "gmail", "return_to": "/settings"},
    )

    assert response.status_code == 409
    assert db.query(MailboxOAuthSession).count() == 0


def test_oauth_rejects_open_redirect_and_wrong_provider_state(api_context, tmp_path):
    client, db, _ = api_context
    provider = FakeOAuthProvider()
    service = oauth_service(db, tmp_path, provider)
    app.dependency_overrides[get_mailbox_oauth_service] = lambda: service

    open_redirect = client.post(
        "/api/v1/mailboxes/oauth/start",
        json={"provider": "gmail", "return_to": "https://evil.example/steal"},
    )
    started = client.post(
        "/api/v1/mailboxes/oauth/start",
        json={"provider": "gmail", "return_to": "/settings"},
    ).json()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    wrong_provider = client.get(
        "/api/v1/mailboxes/oauth/callback/outlook",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert open_redirect.status_code == 422
    assert wrong_provider.status_code == 409
    assert provider.exchanges == []
