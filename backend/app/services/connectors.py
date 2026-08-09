"""Persistent connector control plane with write-only secret storage."""
from datetime import datetime
from pathlib import Path
import math
import os
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.integrations.hunter import HunterClient, HunterConnectorError
from app.models.database import ConnectorConfiguration


class ConnectorCatalogItem(BaseModel):
    provider: str
    display_name: str
    description: str
    capabilities: List[str]


class ConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    secret: SecretStr = Field(min_length=1)
    config: Dict[str, Any] = Field(default_factory=dict)


class ConnectorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    secret: Optional[SecretStr] = Field(default=None, min_length=1)
    config: Optional[Dict[str, Any]] = None


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    provider: str
    name: str
    enabled: bool
    config: Dict[str, Any]
    version: int
    secret_configured: bool
    last_status: str
    last_error_code: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ConnectorProbe(BaseModel):
    ready: bool
    status: str
    error_code: Optional[str] = None
    account: Dict[str, Any] = Field(default_factory=dict)


class ConnectorNotFound(LookupError):
    pass


class ConnectorConflict(RuntimeError):
    pass


class ConnectorService:
    CATALOG = [
        ConnectorCatalogItem(
            provider="hunter",
            display_name="Hunter",
            description="Find and verify business email contacts.",
            capabilities=["domain_search", "email_finder", "email_verifier"],
        )
    ]

    def __init__(self, db: Session, config: Settings = settings) -> None:
        self._db = db
        self._settings = config

    def list(self) -> List[ConnectorResponse]:
        rows = (
            self._db.query(ConnectorConfiguration)
            .order_by(ConnectorConfiguration.created_at.asc())
            .all()
        )
        return [self._response(row) for row in rows]

    def create(
        self,
        command: ConnectorCreate,
        *,
        updated_by_user_id: int,
    ) -> ConnectorResponse:
        provider = command.provider.strip().lower()
        self._validate_provider(provider)
        self._validate_config(command.config)
        connector_id = uuid4()
        secret_ref = self._write_secret(
            connector_id,
            command.secret.get_secret_value(),
        )
        row = ConnectorConfiguration(
            id=connector_id,
            provider=provider,
            name=command.name.strip(),
            enabled=False,
            config_json=dict(command.config),
            secret_ref=str(secret_ref),
            version=1,
            last_status="not_tested",
            updated_by_user_id=updated_by_user_id,
        )
        self._db.add(row)
        try:
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            secret_ref.unlink(missing_ok=True)
            raise ConnectorConflict("Connector name already exists") from exc
        except Exception:
            self._db.rollback()
            secret_ref.unlink(missing_ok=True)
            raise
        self._db.refresh(row)
        return self._response(row)

    def update(
        self,
        connector_id: UUID,
        command: ConnectorUpdate,
        *,
        updated_by_user_id: int,
    ) -> ConnectorResponse:
        row = self._get(connector_id)
        next_name = command.name.strip() if command.name is not None else row.name
        duplicate = (
            self._db.query(ConnectorConfiguration.id)
            .filter(
                ConnectorConfiguration.provider == row.provider,
                ConnectorConfiguration.name == next_name,
                ConnectorConfiguration.id != row.id,
            )
            .first()
        )
        if duplicate is not None:
            raise ConnectorConflict("Connector name already exists")
        previous_secret = None
        if command.secret is not None and self._has_secret(row):
            previous_secret = self._read_secret(row)
        if command.config is not None:
            self._validate_config(command.config)
            row.config_json = dict(command.config)
        if command.name is not None:
            row.name = command.name.strip()
        if command.secret is not None:
            self._write_secret(
                row.id,
                command.secret.get_secret_value(),
                path=Path(row.secret_ref),
            )
        if command.config is not None or command.secret is not None:
            row.enabled = False
            row.last_status = "not_tested"
            row.last_error_code = None
            row.last_tested_at = None
        row.version += 1
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = datetime.utcnow()
        try:
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            if previous_secret is not None:
                self._write_secret(row.id, previous_secret, path=Path(row.secret_ref))
            raise ConnectorConflict("Connector name already exists") from exc
        except Exception:
            self._db.rollback()
            if previous_secret is not None:
                self._write_secret(row.id, previous_secret, path=Path(row.secret_ref))
            raise
        self._db.refresh(row)
        return self._response(row)

    def set_enabled(
        self,
        connector_id: UUID,
        *,
        enabled: bool,
        updated_by_user_id: int,
    ) -> ConnectorResponse:
        row = self._get(connector_id)
        if enabled and not self._has_secret(row):
            raise ValueError("Connector secret is not configured")
        if enabled and row.last_status != "healthy":
            raise ValueError("Connector must pass a healthy connection test")
        if enabled:
            self._db.query(ConnectorConfiguration).filter(
                ConnectorConfiguration.provider == row.provider,
                ConnectorConfiguration.id != row.id,
            ).update({ConnectorConfiguration.enabled: False})
        row.enabled = enabled
        row.version += 1
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(row)
        return self._response(row)

    async def probe(self, connector_id: UUID) -> ConnectorProbe:
        row = self._get(connector_id)
        try:
            client = HunterClient(
                self._read_secret(row),
                timeout_seconds=float((row.config_json or {}).get("timeout_seconds", 15)),
            )
            account = await client.probe()
            row.last_status = "healthy"
            row.last_error_code = None
            result = ConnectorProbe(
                ready=True,
                status="healthy",
                account={
                    key: account[key]
                    for key in ("email", "plan_name", "reset_date", "requests")
                    if key in account
                },
            )
        except (HunterConnectorError, ValueError) as exc:
            row.last_status = "failed"
            error_code = (
                exc.error_code
                if isinstance(exc, HunterConnectorError)
                else "secret_unavailable"
            )
            row.last_error_code = error_code
            result = ConnectorProbe(
                ready=False,
                status="failed",
                error_code=error_code,
            )
        row.last_tested_at = datetime.utcnow()
        self._db.commit()
        return result

    def _get(self, connector_id: UUID) -> ConnectorConfiguration:
        row = self._db.get(ConnectorConfiguration, connector_id)
        if row is None:
            raise ConnectorNotFound("Connector not found")
        return row

    @classmethod
    def _validate_provider(cls, provider: str) -> None:
        if provider not in {item.provider for item in cls.CATALOG}:
            raise ValueError("Unsupported connector provider")

    @staticmethod
    def _validate_config(config: Dict[str, Any]) -> None:
        unknown = set(config) - {"timeout_seconds"}
        if unknown:
            raise ValueError("Unsupported connector configuration")
        try:
            timeout = float(config.get("timeout_seconds", 15))
        except (TypeError, ValueError) as exc:
            raise ValueError("Connector timeout must be numeric") from exc
        if not math.isfinite(timeout) or timeout < 1 or timeout > 60:
            raise ValueError("Connector timeout must be between 1 and 60 seconds")

    def _write_secret(
        self,
        connector_id: UUID,
        value: str,
        *,
        path: Optional[Path] = None,
    ) -> Path:
        value = value.strip()
        if not value:
            raise ValueError("Connector secret cannot be empty")
        secret_dir = Path(self._settings.CONNECTOR_SECRET_DIR)
        secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(secret_dir, 0o700)
        target = path or secret_dir / f"{connector_id}.key"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(value, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        return target

    @staticmethod
    def _has_secret(row: ConnectorConfiguration) -> bool:
        path = Path(row.secret_ref)
        return path.is_file() and path.stat().st_size > 0

    @classmethod
    def _read_secret(cls, row: ConnectorConfiguration) -> str:
        if not cls._has_secret(row):
            raise ValueError("Connector secret is unavailable")
        return Path(row.secret_ref).read_text(encoding="utf-8").strip()

    @classmethod
    def _response(cls, row: ConnectorConfiguration) -> ConnectorResponse:
        return ConnectorResponse(
            id=row.id,
            provider=row.provider,
            name=row.name,
            enabled=row.enabled,
            config=dict(row.config_json or {}),
            version=row.version,
            secret_configured=cls._has_secret(row),
            last_status=row.last_status,
            last_error_code=row.last_error_code,
            last_tested_at=row.last_tested_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
