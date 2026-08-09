"""Persistent, secret-safe AI routing configuration resolved per request."""
from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Dict, List, Literal, Optional
from urllib.parse import urlparse

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db import get_db
from app.integrations.ai_provider import get_ai_provider
from app.integrations.llm_gateway import LLMGatewayClient
from app.models.database import AIRuntimeConfiguration
from app.services.llm.contracts import LLMUseCase, REQUIRED_GATEWAY_USE_CASES
from app.services.llm.service import DirectProviderAdapter


class AIRuntimeConfigUpdate(BaseModel):
    """Admin write contract. The API key is accepted but never serialized back."""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["direct", "omniroute"]
    base_url: str = Field(min_length=1, max_length=500)
    allowed_providers: List[str] = Field(default_factory=list, max_length=50)
    model_aliases: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=60.0, ge=1, le=300)
    api_key: Optional[SecretStr] = Field(default=None, min_length=1)


class AIRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["direct", "omniroute"]
    base_url: str
    allowed_providers: List[str]
    model_aliases: Dict[str, str]
    timeout_seconds: float
    source: Literal["environment", "runtime"]
    version: int
    api_key_configured: bool
    updated_at: Optional[datetime] = None


class AIRuntimeProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    reachable: bool
    models: List[str] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)


class AIRuntimeService:
    """Read the latest DB snapshot so configuration changes need no restart."""

    CONFIGURATION_ID = 1

    def __init__(self, db: Session, config: Settings = settings) -> None:
        self._db = db
        self._settings = config

    def get_config(self) -> AIRuntimeConfig:
        row = self._row()
        if row is None:
            return AIRuntimeConfig(
                backend=self._settings.LLM_BACKEND,
                base_url=self._settings.OMNIROUTE_BASE_URL,
                allowed_providers=list(
                    self._settings.OMNIROUTE_ALLOWED_PROVIDERS
                ),
                model_aliases=self._settings.omniroute_model_aliases(),
                timeout_seconds=self._settings.OMNIROUTE_TIMEOUT_SECONDS,
                source="environment",
                version=0,
                api_key_configured=self._has_api_key(),
            )

        return AIRuntimeConfig(
            backend=row.backend,
            base_url=row.base_url,
            allowed_providers=list(row.allowed_providers or []),
            model_aliases=dict(row.model_aliases or {}),
            timeout_seconds=row.timeout_seconds,
            source="runtime",
            version=row.version,
            api_key_configured=self._has_api_key(),
            updated_at=self._as_utc(row.updated_at),
        )

    def update_config(
        self,
        update: AIRuntimeConfigUpdate,
        *,
        updated_by_user_id: int,
    ) -> AIRuntimeConfig:
        providers = self._normalize_providers(update.allowed_providers)
        aliases = self._validate_aliases(update.model_aliases)
        self._validate_base_url(update.base_url)
        if update.backend == "omniroute":
            self._validate_gateway_policy(providers, aliases)

        if update.api_key is not None:
            self._write_api_key(update.api_key.get_secret_value())

        row = self._row()
        if row is None:
            row = AIRuntimeConfiguration(
                id=self.CONFIGURATION_ID,
                version=1,
                updated_by_user_id=updated_by_user_id,
            )
            self._db.add(row)
        else:
            row.version += 1

        row.backend = update.backend
        row.base_url = update.base_url.rstrip("/")
        row.allowed_providers = providers
        row.model_aliases = aliases
        row.timeout_seconds = update.timeout_seconds
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._db.commit()
        self._db.refresh(row)
        return self.get_config()

    def build_backend(self):
        config = self.get_config()
        if config.backend == "direct":
            return DirectProviderAdapter(get_ai_provider())
        self._validate_gateway_policy(
            config.allowed_providers,
            config.model_aliases,
        )
        return LLMGatewayClient(
            base_url=config.base_url,
            api_key=self._read_api_key(),
            model_aliases={
                LLMUseCase(use_case): model
                for use_case, model in config.model_aliases.items()
            },
            allowed_providers=config.allowed_providers,
            timeout_seconds=config.timeout_seconds,
        )

    async def list_models(self) -> List[str]:
        backend = self.build_backend()
        if not isinstance(backend, LLMGatewayClient):
            return []
        try:
            return sorted(await backend.list_models())
        finally:
            await backend.aclose()

    async def probe(self) -> AIRuntimeProbe:
        config = self.get_config()
        if config.backend == "direct":
            return AIRuntimeProbe(
                ready=True,
                reachable=True,
                issues=["direct_provider_does_not_support_model_discovery"],
            )
        try:
            models = await self.list_models()
        except Exception:
            return AIRuntimeProbe(
                ready=False,
                reachable=False,
                issues=["gateway_probe_failed"],
            )
        missing = sorted(set(config.model_aliases.values()) - set(models))
        return AIRuntimeProbe(
            ready=not missing,
            reachable=True,
            models=models,
            issues=["configured_aliases_not_exposed"] if missing else [],
        )

    def _row(self) -> Optional[AIRuntimeConfiguration]:
        return self._db.get(AIRuntimeConfiguration, self.CONFIGURATION_ID)

    def _secret_path(self) -> Path:
        return Path(self._settings.AI_RUNTIME_SECRET_FILE)

    def _has_api_key(self) -> bool:
        runtime_path = self._secret_path()
        if runtime_path.is_file() and runtime_path.stat().st_size > 0:
            return True
        if self._settings.OMNIROUTE_API_KEY.strip():
            return True
        configured = self._settings.OMNIROUTE_API_KEY_FILE
        return bool(configured and Path(configured).is_file())

    def _read_api_key(self) -> str:
        candidates = [
            self._secret_path(),
            Path(self._settings.OMNIROUTE_API_KEY_FILE)
            if self._settings.OMNIROUTE_API_KEY_FILE
            else None,
        ]
        for path in candidates:
            if path and path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
        value = self._settings.OMNIROUTE_API_KEY.strip()
        if not value:
            raise RuntimeError("OmniRoute API key is not configured")
        return value

    def _write_api_key(self, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("OmniRoute API key cannot be empty")
        path = self._secret_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _normalize_providers(values: List[str]) -> List[str]:
        providers: List[str] = []
        for value in values:
            normalized = value.strip().lower()
            if normalized and normalized not in providers:
                providers.append(normalized)
        return providers

    @staticmethod
    def _validate_aliases(values: Dict[str, str]) -> Dict[str, str]:
        allowed = {use_case.value for use_case in LLMUseCase}
        aliases: Dict[str, str] = {}
        for use_case, value in values.items():
            if use_case not in allowed:
                raise ValueError(f"Unknown AI use case: {use_case}")
            model = value.strip()
            if model.lower().startswith("auto/"):
                raise ValueError("dynamic auto/* routes are not approved")
            if model:
                aliases[use_case] = model
        return aliases

    @staticmethod
    def _validate_gateway_policy(
        providers: List[str], aliases: Dict[str, str]
    ) -> None:
        if not providers:
            raise ValueError("OmniRoute provider allowlist cannot be empty")
        missing = [
            use_case.value
            for use_case in REQUIRED_GATEWAY_USE_CASES
            if use_case.value not in aliases
        ]
        if missing:
            raise ValueError(
                "Required model aliases are missing: " + ", ".join(missing)
            )

    @staticmethod
    def _validate_base_url(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("AI gateway base URL must be HTTP(S)")

    @staticmethod
    def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def get_ai_runtime_service(db: Session = Depends(get_db)) -> AIRuntimeService:
    return AIRuntimeService(db)
