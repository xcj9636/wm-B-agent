"""Evidence-backed media review and quarantine promotion."""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.integrations.object_store import MediaObjectStore
from app.models.database import (
    MediaAsset,
    MediaConsentRecord,
    MediaRightsRecord,
    MediaScanReport,
)
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.assets import (
    MediaAssetConflict,
    MediaAssetForbidden,
    MediaAssetNotFound,
)
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
)


class ScanEvidenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scanner: str = Field(min_length=1, max_length=100)
    scanner_version: str = Field(min_length=1, max_length=100)
    status: AssetScanStatus
    asset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    findings: dict[str, Any] = Field(default_factory=dict)


class RightsEvidenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AssetRightsStatus
    basis: str = Field(min_length=1, max_length=100)
    territories: list[str] = Field(min_length=1, max_length=100)
    channels: list[str] = Field(min_length=1, max_length=100)
    source_ref: str = Field(min_length=1, max_length=500)
    valid_from: datetime
    valid_until: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_window(self) -> "RightsEvidenceCommand":
        _require_aware(self.valid_from)
        if self.valid_until is not None:
            _require_aware(self.valid_until)
            if self.valid_until <= self.valid_from:
                raise ValueError("rights validity window is invalid")
        return self


class ConsentEvidenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_ref: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=500)
    regions: list[str] = Field(min_length=1, max_length=100)
    media_types: list[str] = Field(min_length=1, max_length=20)
    status: AssetConsentStatus
    valid_from: datetime
    valid_until: Optional[datetime] = None
    evidence_asset_id: UUID

    @model_validator(mode="after")
    def validate_window(self) -> "ConsentEvidenceCommand":
        _require_aware(self.valid_from)
        if self.valid_until is not None:
            _require_aware(self.valid_until)
            if self.valid_until <= self.valid_from:
                raise ValueError("consent validity window is invalid")
        return self


class MediaReviewService:
    """Persists review facts and promotes only from referenced evidence."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def record_scan(
        self,
        asset_id: UUID,
        command: ScanEvidenceCommand,
        principal: ExecutionPrincipal,
        *,
        now: Optional[datetime] = None,
    ) -> MediaScanReport:
        self._require_role(principal, {"media_scanner", "admin"}, "scanner")
        asset = self._owned_asset(asset_id, principal)
        if command.asset_sha256 != asset.sha256:
            raise MediaAssetConflict("Scan evidence hash does not match asset")
        report = MediaScanReport(
            org_id=principal.org_id,
            asset_id=asset.id,
            scanner=command.scanner.strip(),
            scanner_version=command.scanner_version.strip(),
            status=command.status.value,
            asset_sha256=command.asset_sha256,
            findings_json=command.findings,
            created_by_user_id=principal.user_id,
            created_at=_naive_utc(now),
        )
        self._db.add(report)
        self._db.commit()
        self._db.refresh(report)
        return report

    def record_rights(
        self,
        asset_id: UUID,
        command: RightsEvidenceCommand,
        principal: ExecutionPrincipal,
        *,
        now: Optional[datetime] = None,
    ) -> MediaRightsRecord:
        self._require_role(principal, {"legal_reviewer", "admin"}, "rights reviewer")
        asset = self._owned_asset(asset_id, principal)
        record = MediaRightsRecord(
            org_id=principal.org_id,
            asset_id=asset.id,
            status=command.status.value,
            basis=command.basis.strip(),
            territories=command.territories,
            channels=command.channels,
            source_ref=command.source_ref.strip(),
            valid_from=_naive_utc(command.valid_from),
            valid_until=_optional_naive_utc(command.valid_until),
            reviewed_by_user_id=principal.user_id,
            created_at=_naive_utc(now),
        )
        self._db.add(record)
        self._db.commit()
        self._db.refresh(record)
        return record

    def record_consent(
        self,
        asset_id: UUID,
        command: ConsentEvidenceCommand,
        principal: ExecutionPrincipal,
        *,
        now: Optional[datetime] = None,
    ) -> MediaConsentRecord:
        self._require_role(principal, {"legal_reviewer", "admin"}, "consent reviewer")
        asset = self._owned_asset(asset_id, principal)
        evidence = self._owned_asset(command.evidence_asset_id, principal)
        record = MediaConsentRecord(
            org_id=principal.org_id,
            asset_id=asset.id,
            subject_ref=command.subject_ref.strip(),
            purpose=command.purpose.strip(),
            regions=command.regions,
            media_types=command.media_types,
            status=command.status.value,
            valid_from=_naive_utc(command.valid_from),
            valid_until=_optional_naive_utc(command.valid_until),
            evidence_asset_id=evidence.id,
            created_by_user_id=principal.user_id,
            created_at=_naive_utc(now),
        )
        self._db.add(record)
        self._db.commit()
        self._db.refresh(record)
        return record

    def promote(
        self,
        asset_id: UUID,
        *,
        scan_report_id: UUID,
        rights_record_id: UUID,
        consent_record_id: Optional[UUID],
        principal: ExecutionPrincipal,
        object_store: MediaObjectStore,
        now: Optional[datetime] = None,
    ) -> MediaAsset:
        self._require_role(principal, {"admin"}, "administrator")
        asset = self._owned_asset(asset_id, principal)
        if not asset.quarantined:
            if (
                asset.scan_report_id == scan_report_id
                and asset.rights_record_id == rights_record_id
                and asset.consent_record_id == consent_record_id
            ):
                return asset
            raise MediaAssetConflict("Asset was promoted with different evidence")
        checked_at = _naive_utc(now)
        scan = self._evidence(MediaScanReport, scan_report_id)
        rights = self._evidence(MediaRightsRecord, rights_record_id)
        self._authorize_evidence(scan, asset, principal)
        self._authorize_evidence(rights, asset, principal)
        self._validate_scan(scan, asset)
        self._validate_rights(rights, checked_at)

        consent = None
        if asset.consent_required:
            if consent_record_id is None:
                raise MediaAssetConflict("Required consent evidence is missing")
            consent = self._evidence(MediaConsentRecord, consent_record_id)
            self._authorize_evidence(consent, asset, principal)
            self._validate_consent(consent, checked_at)
        elif consent_record_id is not None:
            consent = self._evidence(MediaConsentRecord, consent_record_id)
            self._authorize_evidence(consent, asset, principal)
            self._validate_consent(consent, checked_at)

        promoted = object_store.promote(
            asset.storage_key,
            expected_sha256=asset.sha256,
        )
        if not promoted.key.startswith("assets/"):
            raise MediaAssetConflict("Promoted object is outside the asset namespace")
        if promoted.sha256 != asset.sha256:
            raise MediaAssetConflict("Promoted object checksum does not match asset")
        if promoted.size_bytes != asset.size_bytes:
            raise MediaAssetConflict("Promoted object size does not match asset")
        if promoted.content_type.strip().lower() != asset.mime_type:
            raise MediaAssetConflict("Promoted object MIME does not match asset")

        asset.scan_status = AssetScanStatus.PASSED.value
        asset.rights_status = AssetRightsStatus.VERIFIED.value
        asset.consent_status = (
            AssetConsentStatus.VALID.value
            if consent is not None
            else AssetConsentStatus.NOT_REQUIRED.value
        )
        asset.scan_report_id = scan.id
        asset.rights_record_id = rights.id
        asset.consent_record_id = consent.id if consent is not None else None
        asset.storage_key = promoted.key
        asset.quarantined = False
        asset.reviewed_by_user_id = principal.user_id
        asset.reviewed_at = checked_at
        self._db.commit()
        self._db.refresh(asset)
        return asset

    def _owned_asset(
        self,
        asset_id: UUID,
        principal: ExecutionPrincipal,
    ) -> MediaAsset:
        asset = self._db.get(MediaAsset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise MediaAssetNotFound("Media asset was not found")
        if asset.org_id != principal.org_id:
            raise MediaAssetForbidden("Asset is outside the current organization")
        return asset

    def _evidence(self, model, evidence_id: UUID):
        evidence = self._db.get(model, evidence_id)
        if evidence is None:
            raise MediaAssetNotFound("Media review evidence was not found")
        return evidence

    @staticmethod
    def _authorize_evidence(evidence, asset, principal) -> None:
        if evidence.org_id != principal.org_id:
            raise MediaAssetForbidden("Evidence is outside the current organization")
        if evidence.asset_id != asset.id:
            raise MediaAssetConflict("Evidence does not belong to the media asset")

    @staticmethod
    def _validate_scan(report: MediaScanReport, asset: MediaAsset) -> None:
        if report.status != AssetScanStatus.PASSED.value:
            raise MediaAssetConflict("Asset scan has not passed")
        if report.asset_sha256 != asset.sha256:
            raise MediaAssetConflict("Scan evidence hash does not match asset")

    @staticmethod
    def _validate_rights(record: MediaRightsRecord, checked_at: datetime) -> None:
        if record.status != AssetRightsStatus.VERIFIED.value or record.revoked_at:
            raise MediaAssetConflict("Asset rights have not been verified")
        if checked_at < record.valid_from:
            raise MediaAssetConflict("Asset rights are not active")
        if record.valid_until is not None and checked_at >= record.valid_until:
            raise MediaAssetConflict("Asset rights have expired")

    @staticmethod
    def _validate_consent(record: MediaConsentRecord, checked_at: datetime) -> None:
        if record.status != AssetConsentStatus.VALID.value or record.revoked_at:
            raise MediaAssetConflict("Required consent is not valid")
        if checked_at < record.valid_from:
            raise MediaAssetConflict("Required consent is not active")
        if record.valid_until is not None and checked_at >= record.valid_until:
            raise MediaAssetConflict("Required consent has expired")
        if "video" not in {value.strip().lower() for value in record.media_types}:
            raise MediaAssetConflict("Required consent does not cover video")

    @staticmethod
    def _require_role(
        principal: ExecutionPrincipal,
        allowed: set[str],
        label: str,
    ) -> None:
        if not principal.roles.intersection(allowed):
            raise MediaAssetForbidden(f"Media review requires {label} role")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("media review timestamps must be timezone-aware")


def _naive_utc(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    _require_aware(current)
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _optional_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    return _naive_utc(value) if value is not None else None
