"""Construct the configured LLM service without activating it at app startup."""
from pathlib import Path
from typing import Optional

from app.config import settings
from app.integrations.ai_provider import get_ai_provider
from app.integrations.llm_gateway import LLMGatewayClient
from app.services.llm.contracts import LLMUseCase
from app.services.llm.service import DirectProviderAdapter, LLMService


_service: Optional[LLMService] = None
_gateway_client: Optional[LLMGatewayClient] = None


def get_llm_service() -> LLMService:
    """Lazily build the configured backend so non-AI APIs remain available."""
    global _service, _gateway_client
    if _service is not None:
        return _service

    if settings.LLM_BACKEND == "direct":
        _service = LLMService(DirectProviderAdapter(get_ai_provider()))
        return _service

    api_key = _read_gateway_api_key()
    aliases = {
        LLMUseCase(use_case): alias
        for use_case, alias in settings.omniroute_model_aliases().items()
    }
    _gateway_client = LLMGatewayClient(
        base_url=settings.OMNIROUTE_BASE_URL,
        api_key=api_key,
        model_aliases=aliases,
        timeout_seconds=settings.OMNIROUTE_TIMEOUT_SECONDS,
    )
    _service = LLMService(_gateway_client)
    return _service


async def close_llm_service() -> None:
    global _service, _gateway_client
    if _gateway_client is not None:
        await _gateway_client.aclose()
    _service = None
    _gateway_client = None


def _read_gateway_api_key() -> str:
    if settings.OMNIROUTE_API_KEY_FILE:
        try:
            key = Path(settings.OMNIROUTE_API_KEY_FILE).read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            raise RuntimeError("Unable to read the OmniRoute API key file") from exc
    else:
        key = settings.OMNIROUTE_API_KEY.strip()

    if not key:
        raise RuntimeError("OmniRoute is enabled but no API key is configured")
    return key
