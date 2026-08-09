"""Construct the configured LLM service without activating it at app startup."""
from pathlib import Path
from typing import Optional

from app.config import Settings, settings
from app.db import SessionLocal
from app.integrations.ai_provider import get_ai_provider
from app.integrations.llm_gateway import LLMGatewayClient
from app.services.llm.contracts import LLMUseCase, REQUIRED_GATEWAY_USE_CASES
from app.services.llm.instrumented import SessionFactoryInvocationAuditSink
from app.services.llm.service import DirectProviderAdapter, LLMService


_service: Optional[LLMService] = None
_gateway_client: Optional[LLMGatewayClient] = None


def get_llm_service() -> LLMService:
    """Lazily build the configured backend so non-AI APIs remain available."""
    global _service, _gateway_client
    if _service is not None:
        return _service

    if settings.LLM_BACKEND == "direct":
        _service = LLMService(
            DirectProviderAdapter(get_ai_provider()),
            audit_sink=SessionFactoryInvocationAuditSink(SessionLocal),
            backend_name="direct",
        )
        return _service

    _gateway_client = build_gateway_client(settings)
    _service = LLMService(
        _gateway_client,
        audit_sink=SessionFactoryInvocationAuditSink(SessionLocal),
        backend_name="omniroute",
    )
    return _service


def build_gateway_client(config: Settings = settings) -> LLMGatewayClient:
    """Build a new gateway client from validated runtime configuration."""
    _validate_gateway_policy(config)
    api_key = _read_gateway_api_key(config)
    aliases = {
        LLMUseCase(use_case): alias
        for use_case, alias in config.omniroute_model_aliases().items()
    }
    return LLMGatewayClient(
        base_url=config.OMNIROUTE_BASE_URL,
        api_key=api_key,
        model_aliases=aliases,
        allowed_providers=config.OMNIROUTE_ALLOWED_PROVIDERS,
        timeout_seconds=config.OMNIROUTE_TIMEOUT_SECONDS,
    )


def _validate_gateway_policy(config: Settings) -> None:
    if not config.OMNIROUTE_ALLOWED_PROVIDERS:
        raise RuntimeError("OmniRoute provider allowlist is empty")

    aliases = config.omniroute_model_aliases()
    missing = [
        use_case.value
        for use_case in REQUIRED_GATEWAY_USE_CASES
        if use_case.value not in aliases
    ]
    if missing:
        raise RuntimeError(
            "OmniRoute required model aliases are missing: " + ", ".join(missing)
        )


async def close_llm_service() -> None:
    global _service, _gateway_client
    if _gateway_client is not None:
        await _gateway_client.aclose()
    _service = None
    _gateway_client = None


def _read_gateway_api_key(config: Settings = settings) -> str:
    if config.OMNIROUTE_API_KEY_FILE:
        try:
            key = Path(config.OMNIROUTE_API_KEY_FILE).read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            raise RuntimeError("Unable to read the OmniRoute API key file") from exc
    else:
        key = config.OMNIROUTE_API_KEY.strip()

    if not key:
        raise RuntimeError("OmniRoute is enabled but no API key is configured")
    return key
