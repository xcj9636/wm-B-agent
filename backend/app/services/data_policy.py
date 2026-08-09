"""Fail-closed sensitive-data classification, redaction and route policy."""

import re
from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.agent_runtime.contracts import Sensitivity, derive_sensitivity


class SensitiveMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sensitivity: Sensitivity
    matches: List[SensitiveMatch]


class RedactionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    placeholders: List[str]


class DataPolicyUnavailable(RuntimeError):
    pass


_PATTERNS: Tuple[Tuple[str, re.Pattern[str], Sensitivity], ...] = (
    (
        "api_key",
        re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{20,}\b"),
        Sensitivity.RESTRICTED,
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        Sensitivity.CONFIDENTIAL,
    ),
    (
        "phone",
        re.compile(r"(?<!\w)\+?\d[\d\s-]{8,}\d(?!\w)"),
        Sensitivity.CONFIDENTIAL,
    ),
)


class SensitiveDataClassifier:
    def classify(
        self,
        text: str,
        *,
        intrinsic: Sensitivity,
        requested_floor: Optional[Sensitivity] = None,
    ) -> ClassificationResult:
        matches: List[SensitiveMatch] = []
        observed = [intrinsic]
        if requested_floor is not None:
            observed.append(requested_floor)
        for kind, pattern, sensitivity in _PATTERNS:
            found = list(pattern.finditer(text))
            if found:
                observed.append(sensitivity)
                matches.extend(
                    SensitiveMatch(kind=kind, start=item.start(), end=item.end())
                    for item in found
                )
        matches.sort(key=lambda item: (item.start, item.end, item.kind))
        return ClassificationResult(
            sensitivity=derive_sensitivity(*observed),
            matches=matches,
        )


class RedactionVault:
    """Short-lived in-memory placeholder map; durable encrypted vault comes later."""

    def __init__(self) -> None:
        self._values: Dict[str, Dict[str, str]] = {}
        self._counters: Dict[str, Dict[str, int]] = {}

    def redact(self, text: str, *, run_id: str) -> RedactionResult:
        counters = defaultdict(int, self._counters.get(run_id, {}))
        values = self._values.setdefault(run_id, {})
        new_placeholders: List[str] = []
        result = text
        for kind, pattern, _ in _PATTERNS:
            label = kind.upper()

            def replace(match: re.Match[str]) -> str:
                counters[label] += 1
                placeholder = f"[[{label}_{counters[label]}]]"
                values[placeholder] = match.group(0)
                new_placeholders.append(placeholder)
                return placeholder

            result = pattern.sub(replace, result)
        self._counters[run_id] = dict(counters)
        return RedactionResult(text=result, placeholders=new_placeholders)

    def rehydrate(self, text: str, *, run_id: str) -> str:
        if run_id not in self._values:
            raise PermissionError("Redaction mapping is not available for this run")
        result = text
        for placeholder, value in self._values[run_id].items():
            result = result.replace(placeholder, value)
        return result

    def purge(self, *, run_id: str) -> None:
        self._values.pop(run_id, None)
        self._counters.pop(run_id, None)


class ProviderRoutePolicy:
    def __init__(
        self,
        routes: Mapping[Tuple[str, Sensitivity], str],
    ) -> None:
        self._routes = dict(routes)
        self._available = True

    def disable(self) -> None:
        self._available = False

    def resolve(self, use_case: str, sensitivity: Sensitivity) -> str:
        if not self._available:
            raise DataPolicyUnavailable("Provider policy is unavailable")
        route = self._routes.get((use_case, sensitivity))
        if not route:
            raise DataPolicyUnavailable(
                "No approved provider route exists for this sensitivity"
            )
        return route
