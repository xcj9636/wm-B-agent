"""Stable contracts for video personas, storyboards, assets, and generations."""

from datetime import datetime
from enum import Enum
from typing import List
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.agent_runtime.contracts import Sensitivity
from app.services.idempotency import canonical_hash


class VideoWorkflowMode(str, Enum):
    AUTO = "auto"
    TEXT_TO_IMAGE_THEN_IMAGE_TO_VIDEO = "text_to_image_then_image_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"
    REFERENCE_TO_VIDEO = "reference_to_video"


class GenerationMode(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"
    REFERENCE_TO_VIDEO = "reference_to_video"


class MediaAssetKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    PROJECT_FILE = "project_file"


class PersonaStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class AssetScanStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class AssetRightsStatus(str, Enum):
    UNKNOWN = "unknown"
    VERIFIED = "verified"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AssetConsentStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    VALID = "valid"
    REVOKED = "revoked"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class GenerationIngressRequest(BaseModel):
    """Browser contract. Identity, routing, and sensitivity stay server-owned."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=255)
    project_id: UUID
    shot_id: UUID
    requested_mode: VideoWorkflowMode = VideoWorkflowMode.AUTO
    creative_direction: str = Field(min_length=1, max_length=4000)


class PersonaIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=160)
    brand_name: str = Field(min_length=1, max_length=160)
    markets: List[str] = Field(default_factory=list, max_length=50)
    languages: List[str] = Field(default_factory=list, max_length=20)


class PersonaNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tone: List[str] = Field(default_factory=list, max_length=20)
    value_propositions: List[str] = Field(default_factory=list, max_length=30)
    calls_to_action: List[str] = Field(default_factory=list, max_length=20)
    prohibited_claims: List[str] = Field(default_factory=list, max_length=50)


class PersonaVisualBible(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    style: List[str] = Field(default_factory=list, max_length=30)
    palette: List[str] = Field(default_factory=list, max_length=20)
    camera_language: List[str] = Field(default_factory=list, max_length=30)
    forbidden_visuals: List[str] = Field(default_factory=list, max_length=50)


class VideoPersonaSpec(BaseModel):
    """Version payload. Persistence owns revision and approval metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: PersonaIdentity
    audience_segments: List[str] = Field(min_length=1, max_length=30)
    narrative: PersonaNarrative
    visual_bible: PersonaVisualBible
    reference_asset_ids: List[UUID] = Field(default_factory=list, max_length=20)
    default_workflow: VideoWorkflowMode = VideoWorkflowMode.AUTO


class StoryboardShot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1, le=100)
    duration_seconds: int = Field(ge=1, le=120)
    purpose: str = Field(min_length=1, max_length=160)
    workflow_mode: VideoWorkflowMode
    visual_prompt: str = Field(min_length=1, max_length=8000)
    motion_prompt: str = Field(default="", max_length=8000)
    spoken_copy: str = Field(default="", max_length=4000)
    on_screen_copy: str = Field(default="", max_length=2000)
    reference_asset_ids: List[UUID] = Field(default_factory=list, max_length=20)
    business_claims: List[str] = Field(default_factory=list, max_length=30)
    claim_evidence_ids: List[UUID] = Field(default_factory=list, max_length=50)
    constraints: List[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_references_and_claims(self) -> "StoryboardShot":
        if (
            self.workflow_mode
            in {VideoWorkflowMode.IMAGE_TO_VIDEO, VideoWorkflowMode.REFERENCE_TO_VIDEO}
            and not self.reference_asset_ids
        ):
            raise ValueError("selected workflow requires at least one reference asset")
        if self.business_claims and not self.claim_evidence_ids:
            raise ValueError("business claims require evidence")
        if len(set(self.reference_asset_ids)) != len(self.reference_asset_ids):
            raise ValueError("reference assets must be unique")
        return self


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=200)
    total_duration_seconds: int = Field(ge=1, le=3600)
    shots: List[StoryboardShot] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_timeline(self) -> "Storyboard":
        sequences = [shot.sequence for shot in self.shots]
        if sequences != list(range(1, len(self.shots) + 1)):
            raise ValueError("shot sequences must be contiguous and ordered from one")
        if sum(shot.duration_seconds for shot in self.shots) != (
            self.total_duration_seconds
        ):
            raise ValueError("total duration must equal the sum of shot durations")
        return self


class MediaAssetPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: UUID
    org_id: UUID
    scan_status: AssetScanStatus
    rights_status: AssetRightsStatus
    consent_required: bool
    consent_status: AssetConsentStatus
    sensitivity: Sensitivity


class GenerationIntent(BaseModel):
    """Internal atomic generation request created after server-side planning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    shot_id: UUID
    persona_version_id: UUID
    org_id: UUID
    actor_user_id: int = Field(gt=0)
    mode: GenerationMode
    prompt: str = Field(min_length=1, max_length=12000)
    reference_asset_ids: List[UUID] = Field(default_factory=list, max_length=20)
    sensitivity: Sensitivity
    persona_approved: bool
    storyboard_approved: bool

    @model_validator(mode="after")
    def validate_unique_references(self) -> "GenerationIntent":
        if len(set(self.reference_asset_ids)) != len(self.reference_asset_ids):
            raise ValueError("reference assets must be unique")
        return self

    def input_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class MediaPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID = Field(default_factory=uuid4)
    attempt_id: UUID
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_version: str = Field(min_length=1, max_length=100)
    issued_at: datetime
    expires_at: datetime
    sensitivity: Sensitivity
    allowed: bool
    reason_codes: List[str] = Field(min_length=1, max_length=30)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_times(self) -> "MediaPolicyDecision":
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("policy decision timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("policy decision must expire after it is issued")
        return self

    def signing_payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"signature"})
