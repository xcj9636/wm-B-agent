"""Fail-closed authorization for expensive external media submissions."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Set
from uuid import uuid4

from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.idempotency import canonical_hash
from app.services.media.contracts import (
    AssetConsentStatus,
    AssetRightsStatus,
    AssetScanStatus,
    GenerationIntent,
    GenerationMode,
    MediaAssetPolicySnapshot,
    MediaPolicyDecision,
)


class MediaFeatureDisabled(RuntimeError):
    pass


class MediaPolicyInvalid(RuntimeError):
    pass


class MediaPolicyDenied(RuntimeError):
    def __init__(self, reason_codes: Iterable[str]) -> None:
        self.reason_codes = tuple(dict.fromkeys(reason_codes))
        super().__init__("Media submission denied")


class MediaSubmissionPolicy:
    """Issues and verifies short-lived decisions bound to one immutable attempt."""

    def __init__(
        self,
        *,
        submission_enabled: bool,
        policy_version: str,
        signing_key: bytes,
        decision_ttl_seconds: int = 120,
        allowed_roles: Optional[Set[str]] = None,
        allowed_sensitivities: Optional[Set[Sensitivity]] = None,
    ) -> None:
        if not policy_version:
            raise ValueError("policy_version is required")
        if len(signing_key) < 16:
            raise ValueError("media policy signing key is too short")
        if not 1 <= decision_ttl_seconds <= 900:
            raise ValueError("decision_ttl_seconds must be between 1 and 900")
        self._submission_enabled = submission_enabled
        self._policy_version = policy_version
        self._signing_key = signing_key
        self._decision_ttl_seconds = decision_ttl_seconds
        self._allowed_roles = allowed_roles or {"media_operator", "admin"}
        self._allowed_sensitivities = allowed_sensitivities or {
            Sensitivity.PUBLIC,
            Sensitivity.INTERNAL,
        }

    def authorize(
        self,
        principal: ExecutionPrincipal,
        intent: GenerationIntent,
        *,
        assets: Iterable[MediaAssetPolicySnapshot],
        now: Optional[datetime] = None,
    ) -> MediaPolicyDecision:
        if not self._submission_enabled:
            raise MediaFeatureDisabled("Media submission is disabled")
        issued_at = self._aware_now(now)
        reasons = self._denial_reasons(principal, intent, assets)
        if reasons:
            raise MediaPolicyDenied(reasons)

        provisional = MediaPolicyDecision(
            decision_id=uuid4(),
            attempt_id=intent.attempt_id,
            input_hash=intent.input_hash(),
            policy_version=self._policy_version,
            issued_at=issued_at,
            expires_at=issued_at
            + timedelta(seconds=self._decision_ttl_seconds),
            sensitivity=intent.sensitivity,
            allowed=True,
            reason_codes=["policy_approved"],
            signature="0" * 64,
        )
        return provisional.model_copy(
            update={"signature": self._sign(provisional.signing_payload())}
        )

    def verify(
        self,
        decision: MediaPolicyDecision,
        intent: GenerationIntent,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        if not self._submission_enabled:
            raise MediaFeatureDisabled("Media submission is disabled")
        checked_at = self._aware_now(now)
        if not decision.allowed:
            raise MediaPolicyInvalid("Policy decision is not an approval")
        if decision.policy_version != self._policy_version:
            raise MediaPolicyInvalid("Policy version mismatch")
        if decision.attempt_id != intent.attempt_id:
            raise MediaPolicyInvalid("Policy decision is bound to another attempt")
        if decision.input_hash != intent.input_hash():
            raise MediaPolicyInvalid("Generation input changed after authorization")
        if checked_at >= decision.expires_at:
            raise MediaPolicyInvalid("Policy decision expired")
        expected = self._sign(decision.signing_payload())
        if not hmac.compare_digest(expected, decision.signature):
            raise MediaPolicyInvalid("Policy decision signature is invalid")

    def _denial_reasons(
        self,
        principal: ExecutionPrincipal,
        intent: GenerationIntent,
        assets: Iterable[MediaAssetPolicySnapshot],
    ) -> list[str]:
        reasons: list[str] = []
        if intent.org_id != principal.org_id:
            reasons.append("org_mismatch")
        if intent.actor_user_id != principal.user_id:
            reasons.append("actor_mismatch")
        if not principal.roles.intersection(self._allowed_roles):
            reasons.append("role_not_allowed")
        if not intent.persona_approved:
            reasons.append("persona_not_approved")
        if not intent.storyboard_approved:
            reasons.append("storyboard_not_approved")
        if intent.sensitivity not in self._allowed_sensitivities:
            reasons.append("sensitivity_route_not_approved")
        if (
            intent.mode
            in {GenerationMode.IMAGE_TO_VIDEO, GenerationMode.REFERENCE_TO_VIDEO}
            and not intent.reference_asset_ids
        ):
            reasons.append("reference_asset_required")

        by_id = {asset.asset_id: asset for asset in assets}
        for asset_id in intent.reference_asset_ids:
            asset = by_id.get(asset_id)
            if asset is None:
                reasons.append("referenced_asset_missing")
                continue
            if asset.org_id != principal.org_id:
                reasons.append("asset_org_mismatch")
            if asset.scan_status != AssetScanStatus.PASSED:
                reasons.append("asset_scan_not_passed")
            if asset.rights_status != AssetRightsStatus.VERIFIED:
                reasons.append("asset_rights_unverified")
            if asset.consent_required and asset.consent_status != AssetConsentStatus.VALID:
                reasons.append("asset_consent_invalid")
            if self._sensitivity_rank(asset.sensitivity) > self._sensitivity_rank(
                intent.sensitivity
            ):
                reasons.append("sensitivity_underclassified")
        return reasons

    def _sign(self, payload: dict) -> str:
        digest = canonical_hash(payload).encode("ascii")
        return hmac.new(self._signing_key, digest, hashlib.sha256).hexdigest()

    @staticmethod
    def _aware_now(value: Optional[datetime]) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("policy time must be timezone-aware")
        return current

    @staticmethod
    def _sensitivity_rank(value: Sensitivity) -> int:
        return {
            Sensitivity.PUBLIC: 0,
            Sensitivity.INTERNAL: 1,
            Sensitivity.CONFIDENTIAL: 2,
            Sensitivity.RESTRICTED: 3,
        }[value]
