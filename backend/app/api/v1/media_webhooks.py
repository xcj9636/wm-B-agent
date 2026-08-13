"""Unauthenticated-by-JWT, provider-authenticated media callback endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services.media.callbacks import (
    FalJwksClient,
    FalWebhookAuthenticator,
    FalWebhookHeaders,
    FalWebhookVerificationError,
    FalWebhookVerifier,
    MediaCallbackConflict,
    MediaCallbackInboxService,
)


router = APIRouter()


class MediaCallbackAcceptedResponse(BaseModel):
    accepted: bool = True


@lru_cache(maxsize=4)
def _build_fal_webhook_authenticator(
    expected_user_id: str,
    cache_seconds: int,
    timeout_seconds: float,
) -> FalWebhookAuthenticator:
    return FalWebhookAuthenticator(
        FalWebhookVerifier(expected_user_id),
        FalJwksClient(
            cache_seconds=cache_seconds,
            timeout_seconds=timeout_seconds,
        ),
    )


def get_fal_webhook_authenticator() -> FalWebhookAuthenticator:
    if not settings.MEDIA_CALLBACK_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _build_fal_webhook_authenticator(
        settings.MEDIA_FAL_WEBHOOK_USER_ID,
        settings.MEDIA_FAL_JWKS_CACHE_SECONDS,
        settings.MEDIA_FAL_JWKS_TIMEOUT_SECONDS,
    )


async def _bounded_body(request: Request, limit: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    return bytes(body)


@router.post(
    "/fal",
    response_model=MediaCallbackAcceptedResponse,
    status_code=status.HTTP_200_OK,
)
async def accept_fal_media_callback(
    request: Request,
    x_fal_webhook_request_id: str = Header(
        alias="X-Fal-Webhook-Request-Id",
        min_length=1,
        max_length=255,
    ),
    x_fal_webhook_user_id: str = Header(
        alias="X-Fal-Webhook-User-Id",
        min_length=1,
        max_length=255,
    ),
    x_fal_webhook_timestamp: str = Header(
        alias="X-Fal-Webhook-Timestamp",
        min_length=1,
        max_length=20,
    ),
    x_fal_webhook_signature: str = Header(
        alias="X-Fal-Webhook-Signature",
        min_length=128,
        max_length=128,
    ),
    authenticator: FalWebhookAuthenticator = Depends(
        get_fal_webhook_authenticator
    ),
    db: Session = Depends(get_db),
) -> MediaCallbackAcceptedResponse:
    if not settings.MEDIA_CALLBACK_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    body = await _bounded_body(request, settings.MEDIA_CALLBACK_MAX_BODY_BYTES)
    headers = FalWebhookHeaders(
        request_id=x_fal_webhook_request_id,
        user_id=x_fal_webhook_user_id,
        timestamp=x_fal_webhook_timestamp,
        signature=x_fal_webhook_signature,
    )
    try:
        callback = await authenticator.authenticate(
            body=body,
            headers=headers,
            now=datetime.now(timezone.utc),
        )
    except FalWebhookVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid media callback",
        ) from exc
    try:
        MediaCallbackInboxService(db).accept(
            callback,
            now=datetime.now(timezone.utc),
        )
    except MediaCallbackConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflicting media callback",
        ) from exc
    return MediaCallbackAcceptedResponse()
