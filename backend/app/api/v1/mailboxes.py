"""Authenticated mailbox connections with server-side OAuth callbacks."""
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_active_user
from app.config import settings
from app.db import get_db
from app.models.database import Account, User
from app.services.mailbox_oauth import MailboxOAuthError, MailboxOAuthService


router = APIRouter()


class OAuthStartCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["gmail", "outlook"]
    return_to: Literal["/settings"] = "/settings"


class OAuthStartResponse(BaseModel):
    authorization_url: str


class OAuthProviderResponse(BaseModel):
    provider: str
    display_name: str
    configured: bool


class MailboxAccountResponse(BaseModel):
    id: int
    account_type: str
    name: str
    email: Optional[str]
    phone_number: Optional[str]
    is_active: bool
    is_verified: bool
    connection_status: str
    secret_configured: bool
    oauth_scopes: list[str]
    token_expires_at: Optional[datetime]
    last_verified_at: Optional[datetime]
    last_error_code: Optional[str]
    daily_limit: int
    today_sent: int
    created_at: datetime
    updated_at: datetime


def get_mailbox_oauth_service(db: Session = Depends(get_db)) -> MailboxOAuthService:
    return MailboxOAuthService(db, settings)


@router.get("/oauth/providers", response_model=list[OAuthProviderResponse])
async def list_oauth_providers(
    _: User = Depends(get_current_active_user),
    oauth: MailboxOAuthService = Depends(get_mailbox_oauth_service),
):
    return oauth.providers()


@router.post(
    "/oauth/start",
    response_model=OAuthStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_oauth(
    command: OAuthStartCommand,
    current_user: User = Depends(get_current_active_user),
    oauth: MailboxOAuthService = Depends(get_mailbox_oauth_service),
):
    try:
        authorization_url = oauth.start(
            user_id=current_user.id,
            provider=command.provider,
            return_to=command.return_to,
        )
        return OAuthStartResponse(authorization_url=authorization_url)
    except MailboxOAuthError as exc:
        raise HTTPException(status_code=409, detail=exc.error_code) from exc


@router.get("/oauth/callback/{provider}")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    oauth: MailboxOAuthService = Depends(get_mailbox_oauth_service),
):
    try:
        await oauth.complete(provider=provider, state=state, code=code)
    except MailboxOAuthError as exc:
        raise HTTPException(status_code=409, detail=exc.error_code) from exc
    query = urlencode({"mailbox_oauth": "success", "provider": provider})
    location = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/settings?{query}"
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


@router.get("", response_model=list[MailboxAccountResponse])
async def list_mailboxes(
    current_user: User = Depends(get_current_active_user),
    oauth: MailboxOAuthService = Depends(get_mailbox_oauth_service),
):
    return [
        _account_response(account)
        for account in oauth.list_accounts(
            user_id=current_user.id,
            is_superuser=current_user.is_superuser,
        )
    ]


def _account_response(account: Account) -> MailboxAccountResponse:
    ref = account.credential_secret_ref
    secret_configured = bool(ref and Path(ref).is_file() and Path(ref).stat().st_size)
    return MailboxAccountResponse(
        id=account.id,
        account_type=account.account_type,
        name=account.name,
        email=account.email,
        phone_number=account.phone_number,
        is_active=account.is_active,
        is_verified=account.is_verified,
        connection_status=account.connection_status or "reconnect_required",
        secret_configured=secret_configured,
        oauth_scopes=list(account.oauth_scopes_json or []),
        token_expires_at=account.token_expires_at,
        last_verified_at=account.last_verified_at,
        last_error_code=account.last_error_code,
        daily_limit=account.daily_limit,
        today_sent=account.today_sent,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )
