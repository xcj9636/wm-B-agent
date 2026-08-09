"""Conservative delivery adapters for transactional outbox events."""
from typing import Callable, Optional

import httpx
from pydantic import BaseModel, ConfigDict, model_validator

from app.integrations.email_service import get_email_service
from app.integrations.whatsapp_service import get_whatsapp_service
from app.models.database import Account, OutboxEvent
from app.services.outbox import DeliveryFailureKind


class DeliveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    external_message_id: Optional[str] = None
    failure_kind: Optional[DeliveryFailureKind] = None
    error_code: Optional[str] = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "DeliveryResult":
        if self.success and (self.failure_kind or self.error_code):
            raise ValueError("successful delivery cannot contain failure fields")
        if not self.success and (not self.failure_kind or not self.error_code):
            raise ValueError("failed delivery requires kind and error code")
        return self

    @classmethod
    def sent(cls, *, external_message_id: Optional[str] = None) -> "DeliveryResult":
        return cls(success=True, external_message_id=external_message_id)

    @classmethod
    def failed(
        cls,
        kind: DeliveryFailureKind,
        error_code: str,
    ) -> "DeliveryResult":
        return cls(
            success=False,
            failure_kind=kind,
            error_code=error_code,
        )

    @classmethod
    def unknown_after_send(cls, error_code: str) -> "DeliveryResult":
        return cls.failed(DeliveryFailureKind.UNKNOWN_AFTER_SEND, error_code)


class OutboxDeliveryRouter:
    def __init__(
        self,
        *,
        email_service=None,
        account_loader: Optional[Callable[[int], Optional[Account]]] = None,
        email_service_factory: Optional[Callable[[str], object]] = None,
        whatsapp_service=None,
    ) -> None:
        self._email_service = email_service
        self._account_loader = account_loader
        self._email_service_factory = email_service_factory or get_email_service
        self._whatsapp_service = whatsapp_service

    def bind_session(self, session) -> None:
        """Bind account lookup after construction without exposing credentials."""
        self._account_loader = lambda account_id: session.get(Account, account_id)

    async def deliver(self, event: OutboxEvent) -> DeliveryResult:
        try:
            if event.channel == "email":
                return await self._deliver_email(event)
            if event.channel == "whatsapp":
                return await self._deliver_whatsapp(event)
            return DeliveryResult.failed(
                DeliveryFailureKind.PERMANENT,
                "unsupported_delivery_channel",
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return DeliveryResult.failed(
                DeliveryFailureKind.RETRYABLE_BEFORE_SEND,
                "provider_connect_failed",
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                return DeliveryResult.failed(
                    DeliveryFailureKind.RETRYABLE_BEFORE_SEND,
                    "provider_rate_limited",
                )
            if 400 <= status_code < 500:
                return DeliveryResult.failed(
                    DeliveryFailureKind.PERMANENT,
                    "provider_rejected_delivery",
                )
            return DeliveryResult.unknown_after_send("provider_server_error")
        except (KeyError, TypeError, ValueError):
            return DeliveryResult.failed(
                DeliveryFailureKind.PERMANENT,
                "invalid_delivery_payload",
            )
        except Exception:
            return DeliveryResult.unknown_after_send("delivery_exception")

    async def _deliver_email(self, event: OutboxEvent) -> DeliveryResult:
        payload = event.payload_json
        to = self._required_string(payload, "to")
        subject = self._required_string(payload, "subject")
        body = self._required_string(payload, "body")
        account_id = payload.get("account_id")
        if account_id is None:
            return await self._deliver_legacy_email(
                to=to,
                subject=subject,
                body=body,
                payload=payload,
            )
        if not isinstance(account_id, int) or self._account_loader is None:
            return DeliveryResult.failed(
                DeliveryFailureKind.PERMANENT,
                "sender_account_unavailable",
            )
        account = self._account_loader(account_id)
        if account is None or not account.is_active or not account.is_verified:
            return DeliveryResult.failed(
                DeliveryFailureKind.PERMANENT,
                "sender_account_unavailable",
            )
        if account.account_type not in {"gmail", "outlook"}:
            return DeliveryResult.failed(
                DeliveryFailureKind.PERMANENT,
                "unsupported_email_provider",
            )
        access_token = (account.credentials_json or {}).get("access_token")
        if not isinstance(access_token, str) or not access_token:
            return DeliveryResult.failed(
                DeliveryFailureKind.PERMANENT,
                "sender_account_not_connected",
            )
        email_service = self._email_service or self._email_service_factory(
            account.account_type
        )
        result = await email_service.send_email(
            to=to,
            subject=subject,
            body=body,
            html=payload.get("html"),
            from_email=account.email,
            reply_to=payload.get("reply_to"),
            access_token=access_token,
        )
        if not result.get("success"):
            return DeliveryResult.unknown_after_send("email_delivery_failed")
        message_id = result.get("message_id")
        verify_sent = getattr(email_service, "verify_sent", None)
        if not message_id or verify_sent is None:
            return DeliveryResult.unknown_after_send("sent_copy_not_verified")
        verified = await verify_sent(
            message_id=message_id,
            access_token=access_token,
        )
        if not verified:
            return DeliveryResult.unknown_after_send("sent_copy_not_verified")
        return DeliveryResult.sent(external_message_id=message_id)

    async def _deliver_legacy_email(self, *, to, subject, body, payload):
        """Legacy SMTP has no Sent-folder proof and therefore never reports sent."""
        email_service = self._email_service or get_email_service("smtp")
        result = await email_service.send_email(
            to=to,
            subject=subject,
            body=body,
            html=payload.get("html"),
            reply_to=payload.get("reply_to"),
        )
        if not result.get("success"):
            return DeliveryResult.unknown_after_send("email_delivery_failed")
        return DeliveryResult.unknown_after_send("sent_copy_not_verified")

    async def _deliver_whatsapp(self, event: OutboxEvent) -> DeliveryResult:
        payload = event.payload_json
        to = self._required_string(payload, "to")
        text = self._required_string(payload, "text")
        whatsapp_service = self._whatsapp_service or get_whatsapp_service()
        result = await whatsapp_service.send_message(
            to=to,
            text=text,
            preview_url=bool(payload.get("preview_url", False)),
        )
        return DeliveryResult.sent(external_message_id=result.get("message_id"))

    @staticmethod
    def _required_string(payload, key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"missing required field: {key}")
        return value


def get_outbox_delivery_router(session=None) -> OutboxDeliveryRouter:
    loader = None
    if session is not None:
        loader = lambda account_id: session.get(Account, account_id)
    return OutboxDeliveryRouter(account_loader=loader)
