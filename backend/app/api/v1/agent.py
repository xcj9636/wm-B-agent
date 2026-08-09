"""Authenticated Agent Center APIs."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.auth import get_current_active_user
from app.config import settings
from app.core.agent import get_agent
from app.core.skill_base import SkillRegistry
from app.models.database import User


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


@router.get("/overview")
async def get_agent_overview(
    current_user: User = Depends(get_current_active_user),
):
    """Describe the active agent runtime without exposing prompts or credentials."""
    capabilities = _capabilities()
    agent = get_agent()
    runs = agent.list_execution_statuses()
    active_runs = sum(
        run["status"] in {"pending", "running", "paused"}
        for run in runs
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


@router.get("/runs")
async def list_agent_runs(
    current_user: User = Depends(get_current_active_user),
):
    return get_agent().list_execution_statuses()


@router.get("/runs/{execution_id}")
async def get_agent_run(
    execution_id: str,
    current_user: User = Depends(get_current_active_user),
):
    execution = get_agent().get_execution_status(execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return execution
