"""Fail-closed readiness checks for the optional OmniRoute backend."""
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, settings
from app.integrations.llm_gateway import LLMGatewayClient
from app.services.llm.contracts import REQUIRED_GATEWAY_USE_CASES
from app.services.llm.factory import build_gateway_client


class GatewayReadiness(BaseModel):
    """Secret-free status safe to return from an admin-only endpoint."""

    model_config = ConfigDict(extra="forbid")

    backend: str
    enabled: bool
    ready: bool
    reachable: Optional[bool] = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    configured_aliases: Dict[str, str] = Field(default_factory=dict)
    missing_aliases: List[str] = Field(default_factory=list)
    missing_models: List[str] = Field(default_factory=list)
    allowed_providers: List[str] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)


class GatewayStatusService:
    """Probe gateway configuration without affecting application startup."""

    def __init__(
        self,
        config: Settings = settings,
        *,
        client_factory: Optional[Callable[[], LLMGatewayClient]] = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory or (
            lambda: build_gateway_client(self._config)
        )

    async def check(self) -> GatewayReadiness:
        aliases = self._config.omniroute_model_aliases()
        base = {
            "backend": self._config.LLM_BACKEND,
            "configured_aliases": aliases,
            "allowed_providers": self._config.OMNIROUTE_ALLOWED_PROVIDERS,
        }

        if self._config.LLM_BACKEND != "omniroute":
            return GatewayReadiness(
                **base,
                enabled=False,
                ready=False,
                issues=["gateway_disabled"],
            )

        missing_aliases = [
            use_case.value
            for use_case in REQUIRED_GATEWAY_USE_CASES
            if use_case.value not in aliases
        ]
        policy_issues = []
        if not self._config.OMNIROUTE_ALLOWED_PROVIDERS:
            policy_issues.append("provider_allowlist_empty")
        if missing_aliases:
            policy_issues.append("required_aliases_missing")
        if policy_issues:
            return GatewayReadiness(
                **base,
                enabled=True,
                ready=False,
                missing_aliases=missing_aliases,
                issues=policy_issues,
            )

        client = None
        try:
            client = self._client_factory()
            exposed_models = await client.list_models()
        except Exception:
            return GatewayReadiness(
                **base,
                enabled=True,
                ready=False,
                reachable=False,
                issues=["gateway_probe_failed"],
            )
        finally:
            if client is not None:
                await client.aclose()

        missing_models = sorted(set(aliases.values()) - exposed_models)
        issues = ["configured_aliases_not_exposed"] if missing_models else []
        return GatewayReadiness(
            **base,
            enabled=True,
            ready=not issues,
            reachable=True,
            missing_models=missing_models,
            issues=issues,
        )


def get_gateway_status_service() -> GatewayStatusService:
    """FastAPI dependency seam for the gateway management plane."""
    return GatewayStatusService()
