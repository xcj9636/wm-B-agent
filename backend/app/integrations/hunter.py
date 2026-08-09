"""Secret-safe Hunter API adapter with explicit provider error semantics."""
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.database import ConnectorConfiguration


class EmailVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    score: Optional[int] = None
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class HunterConnectorError(RuntimeError):
    """Normalized failure without provider body or credential material."""

    def __init__(
        self,
        *,
        error_code: str,
        retryable: bool,
        legal_restriction: bool = False,
    ) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.legal_restriction = legal_restriction
        super().__init__(error_code)


class HunterClient:
    BASE_URL = "https://api.hunter.io/v2"
    ERROR_MAP = {
        202: ("verification_in_progress", True, False),
        222: ("smtp_result_unexpected", True, False),
        400: ("invalid_request", False, False),
        401: ("authentication_failed", False, False),
        403: ("rate_limited", True, False),
        404: ("not_found", False, False),
        422: ("unprocessable_request", False, False),
        429: ("quota_exhausted", False, False),
        451: ("legal_restriction", False, True),
    }
    VERIFICATION_STATUSES = {
        "valid",
        "invalid",
        "accept_all",
        "webmail",
        "disposable",
        "unknown",
    }

    def __init__(
        self,
        api_key: str,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("Hunter API key is required")
        self._api_key = api_key
        self._http_client = http_client
        self._timeout = timeout_seconds

    async def probe(self) -> Dict[str, Any]:
        return await self._get_data("/account")

    async def domain_search(
        self,
        *,
        domain: Optional[str] = None,
        company: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> Dict[str, Any]:
        if not domain and not company:
            raise ValueError("domain or company is required")
        return await self._get_data(
            "/domain-search",
            params={
                "domain": domain,
                "company": company,
                "limit": max(1, min(limit, 100)),
                "offset": max(offset, 0),
            },
        )

    async def email_finder(
        self,
        *,
        domain: Optional[str] = None,
        company: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        full_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not domain and not company:
            raise ValueError("domain or company is required")
        if not full_name and not (first_name and last_name):
            raise ValueError("full_name or first_name and last_name are required")
        return await self._get_data(
            "/email-finder",
            params={
                "domain": domain,
                "company": company,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
            },
        )

    async def verify_email(self, email: str) -> EmailVerificationResult:
        data = await self._get_data("/email-verifier", params={"email": email})
        details = {
            key: data[key]
            for key in (
                "regexp",
                "gibberish",
                "disposable",
                "webmail",
                "mx_records",
                "smtp_server",
                "smtp_check",
                "accept_all",
                "block",
            )
            if key in data
        }
        status = str(data.get("status") or "unknown").lower()
        if status not in self.VERIFICATION_STATUSES:
            status = "unknown"
        return EmailVerificationResult(
            status=status,
            score=data.get("score"),
            retryable=False,
            details=details,
        )

    async def _get_data(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = await self._request(path, params=params)
        if response.status_code not in {200, 201}:
            self._raise_for_status(response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HunterConnectorError(
                error_code="invalid_provider_response",
                retryable=False,
            ) from exc
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HunterConnectorError(
                error_code="invalid_provider_response",
                retryable=False,
            )
        return data

    async def _request(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]],
    ) -> httpx.Response:
        headers = {"X-API-KEY": self._api_key, "Accept": "application/json"}
        clean_params = {
            key: value for key, value in (params or {}).items() if value is not None
        }
        try:
            if self._http_client is not None:
                return await self._http_client.get(
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    params=clean_params,
                    timeout=self._timeout,
                )
            async with httpx.AsyncClient() as client:
                return await client.get(
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    params=clean_params,
                    timeout=self._timeout,
                )
        except httpx.TimeoutException as exc:
            raise HunterConnectorError(
                error_code="provider_timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise HunterConnectorError(
                error_code="provider_unreachable",
                retryable=True,
            ) from exc

    @classmethod
    def _raise_for_status(cls, status_code: int) -> None:
        if status_code >= 500:
            raise HunterConnectorError(
                error_code="provider_error",
                retryable=True,
            )
        error_code, retryable, legal_restriction = cls.ERROR_MAP.get(
            status_code,
            ("provider_rejected_request", False, False),
        )
        raise HunterConnectorError(
            error_code=error_code,
            retryable=retryable,
            legal_restriction=legal_restriction,
        )


def get_hunter_client(db: Session = Depends(get_db)) -> HunterClient:
    """Resolve the currently enabled Hunter configuration per request."""
    connector = (
        db.query(ConnectorConfiguration)
        .filter(
            ConnectorConfiguration.provider == "hunter",
            ConnectorConfiguration.enabled.is_(True),
        )
        .order_by(ConnectorConfiguration.updated_at.desc())
        .first()
    )
    if connector is None:
        raise HTTPException(status_code=503, detail="Hunter connector is not enabled")
    secret_path = Path(connector.secret_ref)
    if not secret_path.is_file():
        raise HTTPException(status_code=503, detail="Hunter connector secret is unavailable")
    api_key = secret_path.read_text(encoding="utf-8").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Hunter connector secret is unavailable")
    timeout = float((connector.config_json or {}).get("timeout_seconds", 15))
    return HunterClient(api_key, timeout_seconds=timeout)
