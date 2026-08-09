"""AI runtime control plane and authenticated operator chat APIs."""
import json
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import get_current_active_user
from app.models.database import User
from app.services.ai_chat import (
    AIChatMessageResponse,
    AIChatService,
    AIChatSessionResponse,
    get_ai_chat_service,
)
from app.services.ai_runtime import (
    AIRuntimeConfig,
    AIRuntimeConfigUpdate,
    AIRuntimeProbe,
    AIRuntimeService,
    get_ai_runtime_service,
)
from app.services.agent_runtime.turns import TurnBusy
from app.services.idempotency import IdempotencyConflict


router = APIRouter()


class AIModelsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: List[str]


class AIChatSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, max_length=160)


class AIChatMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=30000)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=255)


def _require_admin(user: User) -> None:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/config", response_model=AIRuntimeConfig)
def get_ai_config(
    current_user: User = Depends(get_current_active_user),
    runtime: AIRuntimeService = Depends(get_ai_runtime_service),
):
    _require_admin(current_user)
    return runtime.get_config()


@router.put("/config", response_model=AIRuntimeConfig)
def update_ai_config(
    update: AIRuntimeConfigUpdate,
    current_user: User = Depends(get_current_active_user),
    runtime: AIRuntimeService = Depends(get_ai_runtime_service),
):
    _require_admin(current_user)
    try:
        return runtime.update_config(
            update,
            updated_by_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/config/test", response_model=AIRuntimeProbe)
async def test_ai_config(
    current_user: User = Depends(get_current_active_user),
    runtime: AIRuntimeService = Depends(get_ai_runtime_service),
):
    _require_admin(current_user)
    return await runtime.probe()


@router.get("/models", response_model=AIModelsResponse)
async def list_ai_models(
    current_user: User = Depends(get_current_active_user),
    runtime: AIRuntimeService = Depends(get_ai_runtime_service),
):
    _require_admin(current_user)
    try:
        return AIModelsResponse(models=await runtime.list_models())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI model discovery failed") from exc


@router.get("/chat/sessions", response_model=List[AIChatSessionResponse])
def list_chat_sessions(
    current_user: User = Depends(get_current_active_user),
    chat: AIChatService = Depends(get_ai_chat_service),
):
    return chat.list_sessions(current_user.id)


@router.post(
    "/chat/sessions",
    response_model=AIChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_chat_session(
    request: AIChatSessionCreate,
    current_user: User = Depends(get_current_active_user),
    chat: AIChatService = Depends(get_ai_chat_service),
):
    return chat.create_session(current_user.id, request.title)


@router.get(
    "/chat/sessions/{session_id}", response_model=AIChatSessionResponse
)
def get_chat_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    chat: AIChatService = Depends(get_ai_chat_service),
):
    try:
        return chat.get_session(session_id, current_user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_chat_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    chat: AIChatService = Depends(get_ai_chat_service),
):
    try:
        chat.delete_session(session_id, current_user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/chat/sessions/{session_id}/messages",
    response_model=AIChatMessageResponse,
)
async def create_chat_message(
    session_id: UUID,
    request: AIChatMessageCreate,
    current_user: User = Depends(get_current_active_user),
    chat: AIChatService = Depends(get_ai_chat_service),
):
    try:
        if request.idempotency_key is None:
            return await chat.complete(session_id, current_user.id, request.content)
        return await chat.complete(
            session_id,
            current_user.id,
            request.content,
            idempotency_key=request.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (IdempotencyConflict, TurnBusy) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/chat/sessions/{session_id}/messages/stream")
async def stream_chat_message(
    session_id: UUID,
    request: AIChatMessageCreate,
    current_user: User = Depends(get_current_active_user),
    chat: AIChatService = Depends(get_ai_chat_service),
):
    async def events():
        try:
            stream = (
                chat.stream(session_id, current_user.id, request.content)
                if request.idempotency_key is None
                else chat.stream(
                    session_id,
                    current_user.id,
                    request.content,
                    idempotency_key=request.idempotency_key,
                )
            )
            async for item in stream:
                data = json.dumps(item["data"], ensure_ascii=False, default=str)
                yield f"event: {item['event']}\ndata: {data}\n\n"
        except KeyError as exc:
            data = json.dumps({"detail": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {data}\n\n"
        except Exception:
            data = json.dumps(
                {"detail": "AI stream failed"}, ensure_ascii=False
            )
            yield f"event: error\ndata: {data}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
