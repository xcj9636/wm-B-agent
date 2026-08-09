"""Server-side Gmail and Microsoft OAuth with write-only credential storage."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Callable, Optional, Protocol
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.models.database import Account, MailboxOAuthSession


SUPPORTED_PROVIDERS = ("gmail", "outlook")


class MailboxOAuthError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "oauth_failed") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class OAuthTokenSet:
    access_token: str
    refresh_token: Optional[str]
    expires_at: datetime
    scopes: list[str]


@dataclass(frozen=True)
class MailboxIdentity:
    subject: str
    email: str
    display_name: str


class OAuthProvider(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    async def exchange(self, *, code: str, code_verifier: str) -> OAuthTokenSet: ...

    async def identity(self, *, access_token: str) -> MailboxIdentity: ...

    async def refresh(self, *, refresh_token: str) -> OAuthTokenSet: ...


class _HttpOAuthProvider:
    def __init__(self, config: Settings, provider: str) -> None:
        self.config = config
        self.provider = provider

    @property
    def client_id(self) -> str:
        return (
            self.config.GMAIL_CLIENT_ID
            if self.provider == "gmail"
            else self.config.OUTLOOK_CLIENT_ID
        )

    @property
    def client_secret(self) -> str:
        return (
            self.config.GMAIL_CLIENT_SECRET
            if self.provider == "gmail"
            else self.config.OUTLOOK_CLIENT_SECRET
        )

    @property
    def redirect_uri(self) -> str:
        return (
            self.config.GMAIL_REDIRECT_URI
            if self.provider == "gmail"
            else self.config.OUTLOOK_REDIRECT_URI
        )

    @property
    def scopes(self) -> list[str]:
        if self.provider == "gmail":
            return [
                "openid",
                "email",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.metadata",
            ]
        return [
            "openid",
            "profile",
            "email",
            "offline_access",
            "User.Read",
            "Mail.Send",
            "Mail.ReadBasic",
        ]

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        if self.provider == "gmail":
            base = "https://accounts.google.com/o/oauth2/v2/auth"
            extra = {"access_type": "offline", "prompt": "consent"}
        else:
            tenant = self.config.OUTLOOK_TENANT_ID.strip() or "common"
            base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
            extra = {"response_mode": "query"}
        query = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            **extra,
        }
        return f"{base}?{urlencode(query)}"

    async def exchange(self, *, code: str, code_verifier: str) -> OAuthTokenSet:
        if self.provider == "gmail":
            url = "https://oauth2.googleapis.com/token"
        else:
            tenant = self.config.OUTLOOK_TENANT_ID.strip() or "common"
            url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        form = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        if self.provider == "outlook":
            form["scope"] = " ".join(self.scopes)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, data=form)
        if response.status_code >= 400:
            raise MailboxOAuthError(
                "Provider rejected the authorization code",
                error_code="token_exchange_failed",
            )
        payload = response.json()
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise MailboxOAuthError(
                "Provider did not return an access token",
                error_code="token_response_invalid",
            )
        scope = str(payload.get("scope") or " ".join(self.scopes)).split()
        return OAuthTokenSet(
            access_token=access_token,
            refresh_token=payload.get("refresh_token"),
            expires_at=datetime.utcnow() + timedelta(seconds=int(payload.get("expires_in", 3600))),
            scopes=scope,
        )

    async def identity(self, *, access_token: str) -> MailboxIdentity:
        headers = {"Authorization": f"Bearer {access_token}"}
        if self.provider == "gmail":
            url = "https://openidconnect.googleapis.com/v1/userinfo"
        else:
            url = "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise MailboxOAuthError(
                "Mailbox identity could not be verified",
                error_code="identity_probe_failed",
            )
        payload = response.json()
        if self.provider == "gmail":
            subject = str(payload.get("sub") or "")
            email = str(payload.get("email") or "")
            name = str(payload.get("name") or email)
        else:
            subject = str(payload.get("id") or "")
            email = str(payload.get("mail") or payload.get("userPrincipalName") or "")
            name = str(payload.get("displayName") or email)
        if not subject or not email:
            raise MailboxOAuthError(
                "Mailbox identity response is incomplete",
                error_code="identity_response_invalid",
            )
        return MailboxIdentity(subject=subject, email=email, display_name=name)

    async def refresh(self, *, refresh_token: str) -> OAuthTokenSet:
        if self.provider == "gmail":
            url = "https://oauth2.googleapis.com/token"
        else:
            tenant = self.config.OUTLOOK_TENANT_ID.strip() or "common"
            url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        form = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if self.provider == "outlook":
            form["scope"] = " ".join(self.scopes)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, data=form)
        if response.status_code >= 400:
            raise MailboxOAuthError(
                "Provider rejected the refresh token",
                error_code="token_refresh_failed",
            )
        payload = response.json()
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise MailboxOAuthError(
                "Provider returned an invalid refresh response",
                error_code="token_response_invalid",
            )
        return OAuthTokenSet(
            access_token=access_token,
            refresh_token=payload.get("refresh_token"),
            expires_at=datetime.utcnow() + timedelta(seconds=int(payload.get("expires_in", 3600))),
            scopes=str(payload.get("scope") or " ".join(self.scopes)).split(),
        )


class MailboxOAuthService:
    def __init__(
        self,
        db: Session,
        config: Settings = settings,
        provider_builder: Optional[Callable[[str], OAuthProvider]] = None,
        secret_root: Optional[Path] = None,
    ) -> None:
        self.db = db
        self.config = config
        self.provider_builder = provider_builder
        self.secret_root = Path(secret_root or config.MAILBOX_SECRET_DIR)

    def providers(self) -> list[dict[str, object]]:
        return [
            {
                "provider": provider,
                "display_name": "Gmail" if provider == "gmail" else "Microsoft",
                "configured": self._is_configured(provider),
            }
            for provider in SUPPORTED_PROVIDERS
        ]

    def start(self, *, user_id: int, provider: str, return_to: str) -> str:
        provider = self._validate_provider(provider)
        if not self._is_configured(provider):
            raise MailboxOAuthError(
                "Mailbox OAuth provider is not configured",
                error_code="provider_not_configured",
            )
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        session_id = uuid4()
        verifier_ref = self._write_secret(
            f"oauth-{session_id}.verifier",
            verifier,
        )
        row = MailboxOAuthSession(
            id=session_id,
            user_id=user_id,
            provider=provider,
            state_hash=self._hash_state(state),
            code_verifier_ref=str(verifier_ref),
            return_to=return_to,
            status="pending",
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        self.db.add(row)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            verifier_ref.unlink(missing_ok=True)
            raise
        return self._provider(provider).authorization_url(
            state=state,
            code_challenge=challenge,
        )

    async def complete(self, *, provider: str, state: str, code: str) -> Account:
        provider = self._validate_provider(provider)
        row = (
            self.db.query(MailboxOAuthSession)
            .filter(MailboxOAuthSession.state_hash == self._hash_state(state))
            .one_or_none()
        )
        now = datetime.utcnow()
        if (
            row is None
            or row.provider != provider
            or row.status != "pending"
            or row.expires_at <= now
        ):
            raise MailboxOAuthError(
                "OAuth state is invalid, expired, or already used",
                error_code="oauth_state_invalid",
            )
        verifier_path = Path(row.code_verifier_ref)
        if not verifier_path.is_file():
            raise MailboxOAuthError(
                "OAuth verifier is unavailable",
                error_code="oauth_verifier_unavailable",
            )
        verifier = verifier_path.read_text(encoding="utf-8")
        claimed = (
            self.db.query(MailboxOAuthSession)
            .filter(
                MailboxOAuthSession.id == row.id,
                MailboxOAuthSession.status == "pending",
            )
            .update({MailboxOAuthSession.status: "exchanging"})
        )
        if claimed != 1:
            self.db.rollback()
            raise MailboxOAuthError(
                "OAuth state is already being used",
                error_code="oauth_state_invalid",
            )
        self.db.commit()
        try:
            provider_client = self._provider(provider)
            token_set = await provider_client.exchange(code=code, code_verifier=verifier)
            identity = await provider_client.identity(access_token=token_set.access_token)
            account = self._upsert_account(row.user_id, provider, identity, token_set)
            row.status = "completed"
            row.used_at = now
            row.error_code = None
            self.db.commit()
            self.db.refresh(account)
            return account
        except Exception as exc:
            self.db.rollback()
            persisted = self.db.get(MailboxOAuthSession, row.id)
            if persisted is not None:
                persisted.status = "failed"
                persisted.used_at = now
                persisted.error_code = (
                    exc.error_code if isinstance(exc, MailboxOAuthError) else "oauth_callback_failed"
                )
                self.db.commit()
            if isinstance(exc, MailboxOAuthError):
                raise
            raise MailboxOAuthError(
                "Mailbox authorization could not be completed",
                error_code="oauth_callback_failed",
            ) from exc
        finally:
            verifier_path.unlink(missing_ok=True)

    def list_accounts(self, *, user_id: int, is_superuser: bool) -> list[Account]:
        query = self.db.query(Account)
        if not is_superuser:
            query = query.filter(Account.user_id == user_id)
        return query.order_by(Account.created_at.asc()).all()

    def _upsert_account(
        self,
        user_id: int,
        provider: str,
        identity: MailboxIdentity,
        token_set: OAuthTokenSet,
    ) -> Account:
        account = (
            self.db.query(Account)
            .filter(
                Account.user_id == user_id,
                Account.account_type == provider,
                or_(
                    Account.oauth_subject == identity.subject,
                    Account.email == identity.email,
                ),
            )
            .one_or_none()
        )
        if account is None:
            account = Account(
                user_id=user_id,
                account_type=provider,
                email=identity.email,
                name=identity.display_name or identity.email,
                daily_limit=100,
                today_sent=0,
                is_active=True,
            )
            self.db.add(account)
            self.db.flush()
        secret_ref = self._write_secret(
            f"account-{account.id}.json",
            json.dumps(
                {
                    "access_token": token_set.access_token,
                    "refresh_token": token_set.refresh_token,
                    "expires_at": token_set.expires_at.isoformat(),
                    "scopes": token_set.scopes,
                },
                separators=(",", ":"),
            ),
        )
        account.name = identity.display_name or identity.email
        account.email = identity.email
        account.oauth_subject = identity.subject
        account.oauth_scopes_json = list(token_set.scopes)
        account.token_expires_at = token_set.expires_at
        account.credential_secret_ref = str(secret_ref)
        account.credentials_json = None
        account.connection_status = "connected"
        account.credential_version = int(account.credential_version or 0) + 1
        account.is_active = True
        account.is_verified = True
        account.last_verified_at = datetime.utcnow()
        account.last_error_code = None
        return account

    def _provider(self, provider: str) -> OAuthProvider:
        return (
            self.provider_builder(provider)
            if self.provider_builder is not None
            else _HttpOAuthProvider(self.config, provider)
        )

    def _is_configured(self, provider: str) -> bool:
        if self.provider_builder is not None:
            return True
        if provider == "gmail":
            return bool(self.config.GMAIL_CLIENT_ID.strip() and self.config.GMAIL_CLIENT_SECRET.strip())
        return bool(self.config.OUTLOOK_CLIENT_ID.strip() and self.config.OUTLOOK_CLIENT_SECRET.strip())

    @staticmethod
    def _validate_provider(provider: str) -> str:
        provider = provider.strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise MailboxOAuthError(
                "Unsupported mailbox provider",
                error_code="provider_unsupported",
            )
        return provider

    @staticmethod
    def _hash_state(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    def _write_secret(self, filename: str, value: str) -> Path:
        self.secret_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.secret_root, 0o700)
        target = self.secret_root / filename
        if target.is_symlink():
            raise MailboxOAuthError(
                "Secret target is unsafe",
                error_code="secret_path_unsafe",
            )
        temporary = self.secret_root / f".{filename}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return target


class MailboxCredentialService:
    """Resolve a usable access token without exposing it outside the worker."""

    def __init__(
        self,
        db: Session,
        config: Settings = settings,
        provider_builder: Optional[Callable[[str], OAuthProvider]] = None,
        secret_root: Optional[Path] = None,
    ) -> None:
        self.db = db
        self.config = config
        self.provider_builder = provider_builder
        self.secret_root = Path(secret_root or config.MAILBOX_SECRET_DIR)

    async def access_token(self, account: Account) -> str:
        if (
            account.account_type not in SUPPORTED_PROVIDERS
            or account.connection_status != "connected"
            or not account.credential_secret_ref
        ):
            raise MailboxOAuthError(
                "Mailbox is not connected",
                error_code="sender_account_not_connected",
            )
        path = Path(account.credential_secret_ref)
        if path.is_symlink() or not path.is_file():
            self._mark_error(account, "credential_secret_unavailable")
            raise MailboxOAuthError(
                "Mailbox credential is unavailable",
                error_code="credential_secret_unavailable",
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._mark_error(account, "credential_secret_invalid")
            raise MailboxOAuthError(
                "Mailbox credential is invalid",
                error_code="credential_secret_invalid",
            ) from exc
        token = payload.get("access_token")
        if (
            isinstance(token, str)
            and token
            and account.token_expires_at is not None
            and account.token_expires_at > datetime.utcnow() + timedelta(minutes=5)
        ):
            return token
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            self._mark_error(account, "refresh_token_unavailable", reconnect=True)
            raise MailboxOAuthError(
                "Mailbox must be reconnected",
                error_code="refresh_token_unavailable",
            )
        provider = (
            self.provider_builder(account.account_type)
            if self.provider_builder is not None
            else _HttpOAuthProvider(self.config, account.account_type)
        )
        try:
            refreshed = await provider.refresh(refresh_token=refresh_token)
        except MailboxOAuthError as exc:
            self._mark_error(account, exc.error_code, reconnect=True)
            raise
        preserved_refresh = refreshed.refresh_token or refresh_token
        next_payload = {
            "access_token": refreshed.access_token,
            "refresh_token": preserved_refresh,
            "expires_at": refreshed.expires_at.isoformat(),
            "scopes": refreshed.scopes,
        }
        writer = MailboxOAuthService(
            self.db,
            self.config,
            provider_builder=self.provider_builder,
            secret_root=path.parent,
        )
        writer._write_secret(path.name, json.dumps(next_payload, separators=(",", ":")))
        account.token_expires_at = refreshed.expires_at
        account.oauth_scopes_json = list(refreshed.scopes)
        account.connection_status = "connected"
        account.credential_version = int(account.credential_version or 0) + 1
        account.last_verified_at = datetime.utcnow()
        account.last_error_code = None
        self.db.commit()
        return refreshed.access_token

    def _mark_error(self, account: Account, code: str, *, reconnect: bool = False) -> None:
        account.last_error_code = code
        if reconnect:
            account.connection_status = "reconnect_required"
            account.is_verified = False
        self.db.commit()
