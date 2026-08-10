"""Authenticated Agent Center APIs."""

import json
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_active_user
from app.config import settings
from app.core.agent import get_agent
from app.core.skill_base import SkillRegistry
from app.db import get_db
from app.models.database import User
from app.services.agent_delivery import (
    AgentDeliveryService,
    DeliveryConflict,
    DeliveryCreate,
    DeliveryNotFound,
    DeliveryResponse,
    DeliveryReview,
)
from app.services.agent_runs import (
    AgentRunService,
    AgentRunSummary,
    RunNotFound,
)
from app.services.agent_run_events import AgentRunEventService
from app.services.agent_research import (
    AgentResearchService,
    DraftCreate,
    DraftReview,
    OutreachDraftResponse,
    ResearchConflict,
    ResearchDraftInvalid,
    ResearchEvidenceUpdate,
    ResearchJobCreate,
    ResearchJobResponse,
    ResearchNotFound,
    ResearchReview,
)
from app.services.ai_runtime import AIRuntimeService, get_ai_runtime_service
from app.services.llm.contracts import GatewayError


router = APIRouter()


BUSINESS_PIPELINES = [
    {
        "id": "lead_acquisition",
        "name": "Lead acquisition",
        "description": "Discover, import, normalize and qualify prospect data.",
        "accent": "blue",
        "stages": [
            {"name": "Discover prospects", "skill": "social_scraper"},
            {"name": "Import source records", "skill": "excel_reader"},
            {"name": "Clean and qualify", "skill": "data_cleaner"},
        ],
    },
    {
        "id": "intelligent_outreach",
        "name": "Intelligent outreach",
        "description": "Generate, schedule and reliably deliver personalized outreach.",
        "accent": "green",
        "stages": [
            {"name": "Generate message", "skill": "message_generator"},
            {"name": "Schedule outreach", "skill": "schedule_outreach"},
            {"name": "Deliver safely", "skill": "auto_sender"},
            {"name": "Monitor outcome", "skill": "monitor"},
        ],
    },
    {
        "id": "conversation_conversion",
        "name": "Conversation conversion",
        "description": "Understand intent, retrieve knowledge, reply and escalate when needed.",
        "accent": "orange",
        "stages": [
            {"name": "Analyze intent", "skill": "intent_analysis"},
            {"name": "Retrieve knowledge", "skill": "rag_skill"},
            {"name": "Generate reply", "skill": "ai_reply"},
            {"name": "Human takeover", "skill": "takeover"},
        ],
    },
]


def _capabilities():
    return [
        {
            "name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "category": skill.category,
            "version": skill.version,
            "ready": True,
        }
        for skill in SkillRegistry.list_all().values()
    ]


def _research_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResearchNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ResearchConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ResearchDraftInvalid):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, GatewayError):
        return HTTPException(
            status_code=503,
            detail=f"AI gateway request failed: {exc.kind.value}",
        )
    return HTTPException(status_code=503, detail="AI runtime is unavailable")


def _delivery_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DeliveryNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DeliveryConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=503, detail="Delivery workflow is unavailable")


@router.get("/overview")
async def get_agent_overview(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Describe the active agent runtime without exposing prompts or credentials."""
    capabilities = _capabilities()
    agent = get_agent()
    active_runs = AgentRunService(db).count_active_for_user(
        user_id=current_user.id
    )

    return {
        "agent": {
            "name": "B-agent",
            "description": "B2B revenue acquisition and conversion agent",
            "status": "ready" if capabilities else "degraded",
        },
        "runtime": {
            "mode": "minimal" if settings.START_MINIMAL else "full",
            "registered_skill_count": len(capabilities),
            "registered_workflow_count": len(agent.list_workflows()),
            "active_run_count": active_runs,
        },
        "routing": {
            "backend": settings.LLM_BACKEND,
            "provider_policy": settings.OMNIROUTE_ALLOWED_PROVIDERS,
            "models": {
                "lead_classification": settings.OMNIROUTE_MODEL_LEAD_CLASSIFICATION,
                "message_draft": settings.OMNIROUTE_MODEL_MESSAGE_DRAFT,
                "live_reply": settings.OMNIROUTE_MODEL_LIVE_REPLY,
                "rag_query_rewrite": settings.OMNIROUTE_MODEL_RAG_QUERY_REWRITE,
                "summarization": settings.OMNIROUTE_MODEL_SUMMARIZATION,
            },
        },
        "pipelines": BUSINESS_PIPELINES,
        "capabilities": capabilities,
    }


@router.get("/runs", response_model=List[AgentRunSummary])
def list_agent_runs(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    return AgentRunService(db).list_for_user(user_id=current_user.id)


@router.get(
    "/research-jobs",
    response_model=List[ResearchJobResponse],
)
def list_research_jobs(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    return AgentResearchService(db).list_jobs(user_id=current_user.id)


@router.post(
    "/research-jobs",
    response_model=ResearchJobResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_research_job(
    command: ResearchJobCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return AgentResearchService(db).create_job(
            command,
            user_id=current_user.id,
        )
    except ResearchNotFound as exc:
        raise _research_http_error(exc) from exc


@router.get(
    "/research-jobs/{job_id}",
    response_model=ResearchJobResponse,
)
def get_research_job(
    job_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return AgentResearchService(db).get_job(
            job_id,
            user_id=current_user.id,
        )
    except ResearchNotFound as exc:
        raise _research_http_error(exc) from exc


@router.put(
    "/research-jobs/{job_id}/evidence",
    response_model=ResearchJobResponse,
)
def update_research_evidence(
    job_id: UUID,
    command: ResearchEvidenceUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return AgentResearchService(db).update_evidence(
            job_id,
            command,
            user_id=current_user.id,
        )
    except ResearchNotFound as exc:
        raise _research_http_error(exc) from exc


@router.post(
    "/research-jobs/{job_id}/review",
    response_model=ResearchJobResponse,
)
def review_research_job(
    job_id: UUID,
    command: ResearchReview,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return AgentResearchService(db).review_job(
            job_id,
            command,
            user_id=current_user.id,
        )
    except (ResearchNotFound, ResearchConflict) as exc:
        raise _research_http_error(exc) from exc


@router.post(
    "/research-jobs/{job_id}/drafts",
    response_model=OutreachDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_research_draft(
    job_id: UUID,
    command: DraftCreate,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    runtime: AIRuntimeService = Depends(get_ai_runtime_service),
):
    try:
        draft, created = await AgentResearchService(db).create_draft(
            job_id,
            command,
            user_id=current_user.id,
            runtime=runtime,
        )
        response.status_code = 201 if created else 200
        return draft
    except (
        ResearchNotFound,
        ResearchConflict,
        ResearchDraftInvalid,
        GatewayError,
        RuntimeError,
    ) as exc:
        raise _research_http_error(exc) from exc


@router.patch(
    "/outreach-drafts/{draft_id}/review",
    response_model=OutreachDraftResponse,
)
def review_research_draft(
    draft_id: UUID,
    command: DraftReview,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return AgentResearchService(db).review_draft(
            draft_id,
            command,
            user_id=current_user.id,
        )
    except (ResearchNotFound, ResearchConflict) as exc:
        raise _research_http_error(exc) from exc


@router.get("/deliveries", response_model=List[DeliveryResponse])
def list_agent_deliveries(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    return AgentDeliveryService(db).list_deliveries(user_id=current_user.id)


@router.post(
    "/outreach-drafts/{draft_id}/deliveries",
    response_model=DeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
def prepare_agent_delivery(
    draft_id: UUID,
    command: DeliveryCreate,
    response: Response,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        delivery, created = AgentDeliveryService(db).prepare(
            draft_id,
            command,
            user_id=current_user.id,
        )
        response.status_code = 201 if created else 200
        return delivery
    except (DeliveryNotFound, DeliveryConflict) as exc:
        raise _delivery_http_error(exc) from exc


@router.patch(
    "/deliveries/{delivery_id}/review",
    response_model=DeliveryResponse,
)
def review_agent_delivery(
    delivery_id: UUID,
    command: DeliveryReview,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return AgentDeliveryService(db).review(
            delivery_id,
            command,
            user_id=current_user.id,
        )
    except (DeliveryNotFound, DeliveryConflict) as exc:
        raise _delivery_http_error(exc) from exc


@router.get("/runs/{run_id}", response_model=AgentRunSummary)
def get_agent_run(
    run_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        return AgentRunService(db).get_for_user(
            run_id,
            user_id=current_user.id,
        )
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/events")
def replay_agent_run_events(
    run_id: UUID,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        cursor = int(last_event_id) if last_event_id is not None else 0
        run_summary = AgentRunService(db).get_for_user(
            run_id,
            user_id=current_user.id,
        )
        events = AgentRunEventService(db).list_for_user(
            run_id,
            user_id=current_user.id,
            after_sequence=cursor,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Last-Event-ID must be a non-negative integer",
        ) from exc
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def replay():
        if not events:
            payload = json.dumps(
                {"status": run_summary.status},
                separators=(",", ":"),
            )
            yield f"event: heartbeat\ndata: {payload}\n\n"
            return
        for event in events:
            payload = json.dumps(
                event.data,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield (
                f"id: {event.sequence}\n"
                f"event: {event.event_type}\n"
                f"data: {payload}\n\n"
            )

    return StreamingResponse(
        replay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "X-Agent-Run-Status": run_summary.status,
        },
    )
