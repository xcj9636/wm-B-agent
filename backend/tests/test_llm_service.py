import pytest

from app.services.llm.contracts import (
    LLMRequest,
    LLMResponse,
    LLMUseCase,
)
from app.services.llm.service import (
    DirectProviderAdapter,
    LLMService,
    StreamingUnavailable,
)


class FakeBackend:
    def __init__(self):
        self.requests = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(request_id=request.request_id, content="gateway reply")


class FakeDirectProvider:
    def __init__(self):
        self.messages = None

    async def chat_completion(self, messages):
        self.messages = messages
        return "direct reply"

    async def chat_completion_with_stream(self, messages):
        self.messages = messages
        yield "direct "
        yield "reply"


@pytest.mark.asyncio
async def test_llm_service_builds_a_provider_neutral_request():
    backend = FakeBackend()
    service = LLMService(backend)

    response = await service.complete(
        LLMUseCase.MESSAGE_DRAFT,
        [{"role": "user", "content": "Draft an email"}],
    )

    assert response.content == "gateway reply"
    assert backend.requests[0].use_case == LLMUseCase.MESSAGE_DRAFT
    assert backend.requests[0].messages[0].content == "Draft an email"


@pytest.mark.asyncio
async def test_direct_adapter_preserves_the_same_response_contract():
    provider = FakeDirectProvider()
    adapter = DirectProviderAdapter(provider)
    request = LLMRequest(
        use_case=LLMUseCase.LIVE_REPLY,
        messages=[{"role": "user", "content": "Hello"}],
    )

    response = await adapter.complete(request)

    assert response.content == "direct reply"
    assert response.request_id == request.request_id
    assert provider.messages == [{"role": "user", "content": "Hello"}]


@pytest.mark.asyncio
async def test_direct_adapter_uses_true_provider_stream_chunks():
    provider = FakeDirectProvider()
    service = LLMService(DirectProviderAdapter(provider))

    chunks = [
        chunk
        async for chunk in service.stream(
            LLMUseCase.LIVE_REPLY,
            [{"role": "user", "content": "Hello"}],
        )
    ]

    assert [chunk.delta for chunk in chunks] == ["direct ", "reply"]
    assert chunks[0].request_id == chunks[1].request_id


@pytest.mark.asyncio
async def test_backend_without_stream_fails_capability_check_without_fake_delta():
    backend = FakeBackend()
    service = LLMService(backend)

    with pytest.raises(StreamingUnavailable):
        _ = [
            chunk
            async for chunk in service.stream(
                LLMUseCase.LIVE_REPLY,
                [{"role": "user", "content": "Hello"}],
            )
        ]

    assert backend.requests == []
