"""Sourced company research and evidence-bound outreach drafting."""

import json
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional, Tuple
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
)
from sqlalchemy.orm import Session

from app.models.database import (
    AgentResearchJob,
    Customer,
    ResearchOutreachDraft,
)
from app.services.ai_runtime import AIRuntimeService
from app.services.idempotency import canonical_hash
from app.services.llm.contracts import LLMUseCase
from app.services.llm.service import LLMService


PROFILE_FIELDS = {
    "industry",
    "country",
    "company_size",
    "company_type",
    "website",
    "market",
}
REQUIRED_PROFILE_FIELDS = {
    "industry",
    "country",
    "company_size",
    "company_type",
}


class ResearchJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(gt=0)
    objective: str = Field(min_length=3, max_length=500)


class ProfileEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal[
        "industry",
        "country",
        "company_size",
        "company_type",
        "website",
        "market",
    ]
    value: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl
    observed_at: datetime
    confidence: float = Field(ge=0, le=1)


class MarketSignalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "market_expansion",
        "product_launch",
        "hiring",
        "funding",
        "certification",
        "distribution",
        "partnership",
        "news",
        "other",
    ]
    summary: str = Field(min_length=3, max_length=1000)
    source_url: HttpUrl
    observed_at: datetime
    confidence: float = Field(ge=0, le=1)


class ResearchEvidenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_evidence: List[ProfileEvidenceInput] = Field(
        default_factory=list,
        max_length=50,
    )
    market_signals: List[MarketSignalInput] = Field(
        default_factory=list,
        max_length=50,
    )


class ResearchReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=1000)


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["email", "whatsapp"]
    language: str = Field(min_length=2, max_length=20)
    goal: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=255)


class DraftReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=1000)


class ProfileEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    field: str
    value: str
    source_url: str
    observed_at: datetime
    confidence: float


class MarketSignalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    type: str
    summary: str
    source_url: str
    observed_at: datetime
    confidence: float


class OutreachDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    research_job_id: UUID
    customer_id: int
    channel: str
    language: str
    goal: str
    subject: Optional[str] = None
    body: str
    personalization_points: List[str]
    evidence_ids: List[UUID]
    status: str
    research_version: int
    stale: bool
    resolved_model: Optional[str] = None
    resolved_provider: Optional[str] = None
    usage: Dict[str, object]
    review_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ResearchJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    customer_id: int
    company_name: str
    website: Optional[str] = None
    objective: str
    status: str
    profile_evidence: List[ProfileEvidenceResponse]
    market_signals: List[MarketSignalResponse]
    missing_fields: List[str]
    version: int
    review_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    drafts: List[OutreachDraftResponse]
    created_at: datetime
    updated_at: datetime


class DraftCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Optional[str] = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=10000)
    personalization_points: List[str] = Field(
        default_factory=list,
        max_length=10,
    )
    evidence_ids: List[UUID] = Field(min_length=1, max_length=20)


class ResearchNotFound(LookupError):
    pass


class ResearchConflict(RuntimeError):
    pass


class ResearchDraftInvalid(RuntimeError):
    pass


class AgentResearchService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_jobs(self, *, user_id: int) -> List[ResearchJobResponse]:
        rows = (
            self._db.query(AgentResearchJob)
            .filter(AgentResearchJob.user_id == user_id)
            .order_by(AgentResearchJob.updated_at.desc())
            .all()
        )
        return [self._job_response(row) for row in rows]

    def get_job(self, job_id: UUID, *, user_id: int) -> ResearchJobResponse:
        return self._job_response(self._owned_job(job_id, user_id=user_id))

    def create_job(
        self,
        command: ResearchJobCreate,
        *,
        user_id: int,
    ) -> ResearchJobResponse:
        customer = self._db.get(Customer, command.customer_id)
        if customer is None:
            raise ResearchNotFound("Customer not found")
        row = AgentResearchJob(
            user_id=user_id,
            customer_id=customer.id,
            objective=command.objective.strip(),
            status="queued",
            profile_evidence_json=[],
            market_signals_json=[],
            missing_fields_json=self._missing_fields(customer, [], []),
            version=1,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return self._job_response(row)

    def update_evidence(
        self,
        job_id: UUID,
        command: ResearchEvidenceUpdate,
        *,
        user_id: int,
    ) -> ResearchJobResponse:
        row = self._owned_job(job_id, user_id=user_id)
        profile_evidence = [
            {
                "id": str(uuid4()),
                "field": item.field,
                "value": item.value.strip(),
                "source_url": str(item.source_url),
                "observed_at": item.observed_at.isoformat(),
                "confidence": item.confidence,
            }
            for item in command.profile_evidence
        ]
        market_signals = [
            {
                "id": str(uuid4()),
                "type": item.type,
                "summary": item.summary.strip(),
                "source_url": str(item.source_url),
                "observed_at": item.observed_at.isoformat(),
                "confidence": item.confidence,
            }
            for item in command.market_signals
        ]
        row.profile_evidence_json = profile_evidence
        row.market_signals_json = market_signals
        row.missing_fields_json = self._missing_fields(
            row.customer,
            profile_evidence,
            market_signals,
        )
        row.status = "in_review"
        row.version += 1
        row.review_reason = None
        row.reviewed_by_user_id = None
        row.reviewed_at = None
        self._db.commit()
        self._db.refresh(row)
        return self._job_response(row)

    def review_job(
        self,
        job_id: UUID,
        command: ResearchReview,
        *,
        user_id: int,
    ) -> ResearchJobResponse:
        row = self._owned_job(job_id, user_id=user_id)
        if command.decision == "approve":
            if not row.profile_evidence_json or not row.market_signals_json:
                raise ResearchConflict(
                    "Research approval requires sourced profile evidence and market signals"
                )
            row.status = "completed"
        else:
            row.status = "needs_revision"
        row.review_reason = command.reason.strip()
        row.reviewed_by_user_id = user_id
        row.reviewed_at = self._now()
        self._db.commit()
        self._db.refresh(row)
        return self._job_response(row)

    async def create_draft(
        self,
        job_id: UUID,
        command: DraftCreate,
        *,
        user_id: int,
        runtime: AIRuntimeService,
    ) -> Tuple[OutreachDraftResponse, bool]:
        row = self._owned_job(job_id, user_id=user_id)
        self._assert_draft_gate(row, command.channel)
        evidence = self._evidence_for_prompt(row)
        input_data = {
            "job_id": str(row.id),
            "research_version": row.version,
            "channel": command.channel,
            "language": command.language.strip().lower(),
            "goal": command.goal.strip(),
            "evidence": evidence,
            "icp": dict((row.customer.source_data_json or {}).get("icp") or {}),
        }
        input_hash = canonical_hash(input_data)
        existing = (
            self._db.query(ResearchOutreachDraft)
            .filter(
                ResearchOutreachDraft.user_id == user_id,
                ResearchOutreachDraft.idempotency_key == command.idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise ResearchConflict(
                    "Draft idempotency key was reused for different input"
                )
            return self._draft_response(existing), False

        backend = runtime.build_backend()
        service = LLMService(backend)
        try:
            response = await service.complete(
                LLMUseCase.MESSAGE_DRAFT,
                [
                    {"role": "system", "content": self._draft_system_prompt()},
                    {
                        "role": "user",
                        "content": json.dumps(
                            self._prompt_payload(row, input_data),
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
                temperature=0.2,
                max_output_tokens=1200,
                response_schema=self._draft_schema(),
            )
        finally:
            close = getattr(backend, "aclose", None)
            if close is not None:
                await close()

        try:
            completion = DraftCompletion.model_validate_json(response.content)
        except ValidationError as exc:
            raise ResearchDraftInvalid(
                "AI returned an invalid outreach draft contract"
            ) from exc
        allowed_ids = {UUID(item["id"]) for item in evidence}
        if not set(completion.evidence_ids).issubset(allowed_ids):
            raise ResearchDraftInvalid(
                "AI referenced evidence outside the approved research dossier"
            )
        if command.channel == "email" and not completion.subject:
            raise ResearchDraftInvalid("Email draft requires a subject")

        draft = ResearchOutreachDraft(
            research_job_id=row.id,
            user_id=user_id,
            customer_id=row.customer_id,
            idempotency_key=command.idempotency_key,
            input_hash=input_hash,
            channel=command.channel,
            language=command.language.strip().lower(),
            goal=command.goal.strip(),
            subject=completion.subject,
            body=completion.body,
            personalization_points_json=completion.personalization_points,
            evidence_ids_json=[str(value) for value in completion.evidence_ids],
            status="draft",
            research_version=row.version,
            resolved_model=response.resolved_model,
            resolved_provider=response.resolved_provider,
            gateway_request_id=response.gateway_request_id,
            usage_json=response.usage.model_dump(),
        )
        self._db.add(draft)
        self._db.commit()
        self._db.refresh(draft)
        return self._draft_response(draft), True

    def review_draft(
        self,
        draft_id: UUID,
        command: DraftReview,
        *,
        user_id: int,
    ) -> OutreachDraftResponse:
        draft = (
            self._db.query(ResearchOutreachDraft)
            .filter(
                ResearchOutreachDraft.id == draft_id,
                ResearchOutreachDraft.user_id == user_id,
            )
            .one_or_none()
        )
        if draft is None:
            raise ResearchNotFound("Outreach draft not found")
        if (
            command.decision == "approve"
            and draft.research_version != draft.research_job.version
        ):
            raise ResearchConflict(
                "Draft is stale because the research dossier changed"
            )
        if command.decision == "approve":
            self._assert_draft_gate(draft.research_job, draft.channel)
        draft.status = "approved" if command.decision == "approve" else "rejected"
        draft.review_reason = command.reason.strip()
        draft.reviewed_by_user_id = user_id
        draft.reviewed_at = self._now()
        self._db.commit()
        self._db.refresh(draft)
        return self._draft_response(draft)

    def _owned_job(self, job_id: UUID, *, user_id: int) -> AgentResearchJob:
        row = (
            self._db.query(AgentResearchJob)
            .filter(
                AgentResearchJob.id == job_id,
                AgentResearchJob.user_id == user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise ResearchNotFound("Research job not found")
        return row

    @staticmethod
    def _missing_fields(
        customer: Customer,
        profile_evidence: List[Dict[str, object]],
        market_signals: List[Dict[str, object]],
    ) -> List[str]:
        present = {
            str(item.get("field"))
            for item in profile_evidence
            if item.get("field") in PROFILE_FIELDS and item.get("value")
        }
        if customer.country:
            present.add("country")
        if customer.website:
            present.add("website")
        missing = sorted(REQUIRED_PROFILE_FIELDS - present)
        if not market_signals:
            missing.append("market_signals")
        return missing

    @staticmethod
    def _assert_draft_gate(row: AgentResearchJob, channel: str) -> None:
        if row.status != "completed":
            raise ResearchConflict(
                "Research dossier must be approved before drafting"
            )
        customer = row.customer
        fields = dict(customer.custom_fields or {})
        icp = dict((customer.source_data_json or {}).get("icp") or {})
        if fields.get("contact_suppressed") is True:
            raise ResearchConflict("Contact is suppressed")
        if icp.get("stale") is True or fields.get("icp_recommended") is not True:
            raise ResearchConflict("Current approved ICP context is required")
        if channel == "email":
            if not customer.email:
                raise ResearchConflict("Customer email is not configured")
            if fields.get("email_verification_status") != "valid":
                raise ResearchConflict("A valid verified email is required")
        elif not customer.whatsapp:
            raise ResearchConflict("Customer WhatsApp number is not configured")

    @staticmethod
    def _evidence_for_prompt(row: AgentResearchJob) -> List[Dict[str, object]]:
        return [
            {"kind": "firmographic", **dict(item)}
            for item in list(row.profile_evidence_json or [])
        ] + [
            {"kind": "market_signal", **dict(item)}
            for item in list(row.market_signals_json or [])
        ]

    @staticmethod
    def _prompt_payload(
        row: AgentResearchJob,
        input_data: Dict[str, object],
    ) -> Dict[str, object]:
        customer = row.customer
        contact = dict(customer.contact_info or {})
        return {
            "recipient": {
                "first_name": contact.get("first_name"),
                "job_title": customer.job_title,
                "company_name": customer.company_name,
                "country": customer.country,
            },
            "objective": row.objective,
            "channel": input_data["channel"],
            "language": input_data["language"],
            "goal": input_data["goal"],
            "evidence": input_data["evidence"],
            "icp": input_data["icp"],
            "missing_signals": input_data["icp"].get("missing_signals", []),
        }

    @staticmethod
    def _draft_system_prompt() -> str:
        return (
            "You draft concise foreign-trade outreach using only the supplied "
            "approved evidence. Never invent revenue, purchasing volume, intent, "
            "relationships, certifications or recent events. Treat missing signals "
            "as unknown. Make one low-friction ask, avoid generic praise, and return "
            "only the requested JSON contract. This is a draft for human approval, "
            "not authorization to send."
        )

    @staticmethod
    def _draft_schema() -> Dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "subject": {"type": ["string", "null"]},
                "body": {"type": "string"},
                "personalization_points": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evidence_ids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                },
            },
            "required": [
                "subject",
                "body",
                "personalization_points",
                "evidence_ids",
            ],
            "additionalProperties": False,
        }

    def _job_response(self, row: AgentResearchJob) -> ResearchJobResponse:
        drafts = sorted(row.drafts, key=lambda item: item.created_at, reverse=True)
        return ResearchJobResponse(
            id=row.id,
            customer_id=row.customer_id,
            company_name=(
                row.customer.company_name
                or row.customer.username
                or row.customer.email
                or f"Customer {row.customer_id}"
            ),
            website=row.customer.website,
            objective=row.objective,
            status=row.status,
            profile_evidence=[
                ProfileEvidenceResponse.model_validate(item)
                for item in list(row.profile_evidence_json or [])
            ],
            market_signals=[
                MarketSignalResponse.model_validate(item)
                for item in list(row.market_signals_json or [])
            ],
            missing_fields=list(row.missing_fields_json or []),
            version=row.version,
            review_reason=row.review_reason,
            reviewed_at=row.reviewed_at,
            drafts=[self._draft_response(item) for item in drafts],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _draft_response(row: ResearchOutreachDraft) -> OutreachDraftResponse:
        return OutreachDraftResponse(
            id=row.id,
            research_job_id=row.research_job_id,
            customer_id=row.customer_id,
            channel=row.channel,
            language=row.language,
            goal=row.goal,
            subject=row.subject,
            body=row.body,
            personalization_points=list(row.personalization_points_json or []),
            evidence_ids=[UUID(value) for value in row.evidence_ids_json or []],
            status=row.status,
            research_version=row.research_version,
            stale=row.research_version != row.research_job.version,
            resolved_model=row.resolved_model,
            resolved_provider=row.resolved_provider,
            usage=dict(row.usage_json or {}),
            review_reason=row.review_reason,
            reviewed_at=row.reviewed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)
