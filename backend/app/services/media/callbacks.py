"""Authenticated provider callback hints for durable media reconciliation."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any, Mapping, Sequence
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.database import (
    MediaCallbackInbox,
    MediaGenerationEvent,
    MediaGenerationJob,
)


FAL_JWKS_URL = "https://rest.fal.ai/.well-known/jwks.json"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,255}\Z")
_SIGNATURE = re.compile(r"[0-9a-fA-F]{128}\Z")
_ED25519_JWK_X = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class FalWebhookVerificationError(ValueError):
    """A callback failed authentication or strict contract validation."""


class MediaCallbackConflict(RuntimeError):
    """A provider reused one request ID for a different signed callback."""


class FalWebhookHeaders(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    timestamp: str = Field(min_length=1, max_length=20)
    signature: str = Field(min_length=128, max_length=128)


class _FalCallbackEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str = Field(min_length=1, max_length=255)
    status: str


@dataclass(frozen=True)
class FalVerifiedCallback:
    provider_request_id: str
    provider_account_ref_hash: str
    body_sha256: str
    delivery_hint: str
    signature_timestamp: datetime


@dataclass(frozen=True)
class MediaCallbackAcceptResult:
    receipt_id: UUID
    job_id: UUID | None
    created: bool


class FalWebhookVerifier:
    """Verify the exact fal.ai Ed25519 webhook signing contract."""

    def __init__(self, expected_user_id: str, *, tolerance_seconds: int = 300):
        expected = expected_user_id.strip()
        if not _IDENTIFIER.fullmatch(expected):
            raise ValueError("expected Fal webhook user ID is invalid")
        if not 1 <= tolerance_seconds <= 300:
            raise ValueError("webhook tolerance must be between 1 and 300 seconds")
        self._expected_user_id = expected
        self._tolerance_seconds = tolerance_seconds

    def verify(
        self,
        *,
        body: bytes,
        headers: FalWebhookHeaders,
        jwks: Sequence[Mapping[str, Any]],
        now: datetime,
    ) -> FalVerifiedCallback:
        try:
            self._validate_identifier(headers.request_id)
            self._validate_identifier(headers.user_id)
            if not hmac.compare_digest(headers.user_id, self._expected_user_id):
                raise FalWebhookVerificationError("webhook account mismatch")
            if not headers.timestamp.isdecimal():
                raise FalWebhookVerificationError("invalid webhook timestamp")
            timestamp_value = int(headers.timestamp)
            now_utc = self._utc(now)
            signature_time = datetime.fromtimestamp(timestamp_value, timezone.utc)
            if (
                abs((now_utc - signature_time).total_seconds())
                > self._tolerance_seconds
            ):
                raise FalWebhookVerificationError("webhook timestamp outside tolerance")
            if not _SIGNATURE.fullmatch(headers.signature):
                raise FalWebhookVerificationError("invalid webhook signature encoding")
            body_hash = hashlib.sha256(body).hexdigest()
            message = (
                f"{headers.request_id}\n{headers.user_id}\n"
                f"{headers.timestamp}\n{body_hash}"
            ).encode()
            signature = bytes.fromhex(headers.signature)
            if not self._verify_with_any_key(message, signature, jwks):
                raise FalWebhookVerificationError("webhook signature mismatch")
            envelope = _FalCallbackEnvelope.model_validate_json(body)
            if not hmac.compare_digest(envelope.request_id, headers.request_id):
                raise FalWebhookVerificationError("callback request ID mismatch")
            if envelope.status not in {"OK", "ERROR"}:
                raise FalWebhookVerificationError("callback status is invalid")
        except FalWebhookVerificationError:
            raise
        except (OverflowError, OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise FalWebhookVerificationError("invalid webhook contract") from exc
        return FalVerifiedCallback(
            provider_request_id=headers.request_id,
            provider_account_ref_hash=hashlib.sha256(
                headers.user_id.encode()
            ).hexdigest(),
            body_sha256=body_hash,
            delivery_hint=envelope.status,
            signature_timestamp=signature_time,
        )

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise FalWebhookVerificationError("invalid webhook identifier")

    @staticmethod
    def _verify_with_any_key(
        message: bytes,
        signature: bytes,
        jwks: Sequence[Mapping[str, Any]],
    ) -> bool:
        if not 1 <= len(jwks) <= 20:
            raise FalWebhookVerificationError("invalid webhook key set")
        valid_key_seen = False
        for jwk in jwks:
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                continue
            encoded = jwk.get("x")
            if not isinstance(encoded, str) or not _ED25519_JWK_X.fullmatch(
                encoded
            ):
                continue
            try:
                key_bytes = base64.urlsafe_b64decode(
                    encoded + "=" * (-len(encoded) % 4)
                )
                if len(key_bytes) != 32:
                    continue
                public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
            except (ValueError, TypeError):
                continue
            valid_key_seen = True
            try:
                public_key.verify(signature, message)
                return True
            except InvalidSignature:
                continue
        if not valid_key_seen:
            raise FalWebhookVerificationError("no valid webhook verification key")
        return False

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class FalJwksClient:
    """Small bounded cache around fal's fixed-origin public key endpoint."""

    def __init__(self, *, cache_seconds: int, timeout_seconds: float):
        if not 60 <= cache_seconds <= 86_400:
            raise ValueError("JWKS cache interval is invalid")
        self._cache_seconds = cache_seconds
        self._timeout_seconds = timeout_seconds
        self._keys: list[Mapping[str, Any]] | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_keys(self) -> list[Mapping[str, Any]]:
        loop_time = asyncio.get_running_loop().time()
        if self._keys is not None and loop_time < self._expires_at:
            return self._keys
        async with self._lock:
            loop_time = asyncio.get_running_loop().time()
            if self._keys is not None and loop_time < self._expires_at:
                return self._keys
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(FAL_JWKS_URL)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise FalWebhookVerificationError(
                    "webhook key service unavailable"
                ) from exc
            if len(response.content) > 65_536:
                raise FalWebhookVerificationError("webhook key response too large")
            try:
                payload = json.loads(response.content)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise FalWebhookVerificationError("invalid webhook key response") from exc
            keys = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(keys, list) or not 1 <= len(keys) <= 20:
                raise FalWebhookVerificationError("invalid webhook key response")
            normalized = [key for key in keys if isinstance(key, dict)]
            if not normalized:
                raise FalWebhookVerificationError("invalid webhook key response")
            self._keys = normalized
            self._expires_at = loop_time + self._cache_seconds
            return normalized


class FalWebhookAuthenticator:
    def __init__(self, verifier: FalWebhookVerifier, jwks_client: FalJwksClient):
        self._verifier = verifier
        self._jwks_client = jwks_client

    async def authenticate(
        self,
        *,
        body: bytes,
        headers: FalWebhookHeaders,
        now: datetime,
    ) -> FalVerifiedCallback:
        keys = await self._jwks_client.get_keys()
        return self._verifier.verify(body=body, headers=headers, jwks=keys, now=now)


class MediaCallbackInboxService:
    """Persist a verified hint once and wake only safe provider polling."""

    def __init__(self, db: Session):
        self._db = db

    def accept(
        self,
        callback: FalVerifiedCallback,
        *,
        now: datetime,
    ) -> MediaCallbackAcceptResult:
        now_naive = self._naive_utc(now)
        existing = self._find_receipt(callback)
        if existing is not None:
            return self._replay_result(existing, callback)
        receipt = MediaCallbackInbox(
            provider="fal",
            provider_account_ref_hash=callback.provider_account_ref_hash,
            provider_request_id=callback.provider_request_id,
            body_sha256=callback.body_sha256,
            delivery_hint=callback.delivery_hint,
            signature_timestamp=self._naive_utc(callback.signature_timestamp),
            received_at=now_naive,
        )
        try:
            with self._db.begin_nested():
                self._db.add(receipt)
                self._db.flush()
        except IntegrityError:
            existing = self._find_receipt(callback)
            if existing is None:
                raise
            return self._replay_result(existing, callback)

        jobs = (
            self._db.query(MediaGenerationJob)
            .filter(
                MediaGenerationJob.provider == "fal",
                MediaGenerationJob.provider_request_id == callback.provider_request_id,
            )
            .with_for_update()
            .limit(2)
            .all()
        )
        job = jobs[0] if len(jobs) == 1 else None
        if job is not None:
            receipt.job_id = job.id
            if job.status == "submitted":
                job.next_reconcile_at = now_naive
                job.updated_at = now_naive
                job.event_sequence += 1
                self._db.add(
                    MediaGenerationEvent(
                        job_id=job.id,
                        sequence=job.event_sequence,
                        event_type="provider.callback_verified",
                        data_json={},
                        created_at=now_naive,
                    )
                )
        self._db.commit()
        return self._result(receipt, created=True)

    def _find_receipt(
        self, callback: FalVerifiedCallback
    ) -> MediaCallbackInbox | None:
        return (
            self._db.query(MediaCallbackInbox)
            .filter(
                MediaCallbackInbox.provider == "fal",
                MediaCallbackInbox.provider_account_ref_hash
                == callback.provider_account_ref_hash,
                MediaCallbackInbox.provider_request_id
                == callback.provider_request_id,
            )
            .one_or_none()
        )

    @staticmethod
    def _result(
        receipt: MediaCallbackInbox, *, created: bool
    ) -> MediaCallbackAcceptResult:
        return MediaCallbackAcceptResult(
            receipt_id=receipt.id,
            job_id=receipt.job_id,
            created=created,
        )

    @classmethod
    def _replay_result(
        cls,
        receipt: MediaCallbackInbox,
        callback: FalVerifiedCallback,
    ) -> MediaCallbackAcceptResult:
        if (
            not hmac.compare_digest(receipt.body_sha256, callback.body_sha256)
            or receipt.delivery_hint != callback.delivery_hint
        ):
            raise MediaCallbackConflict("callback request ID was reused")
        return cls._result(receipt, created=False)

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
