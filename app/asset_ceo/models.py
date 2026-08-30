from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PropertyIdentity:
    property_id: str
    canonical_key: str
    address: str
    city: str = ""
    state: str = "KS"
    zip_code: str = ""
    llc: str = ""
    active: bool = True
    source_system: str = ""
    source_ref: str = ""

    @property
    def display_address(self) -> str:
        return ", ".join(part for part in [self.address, self.city, self.state, self.zip_code] if part)


@dataclass(frozen=True)
class FactInput:
    fact_name: str
    value: Any
    source_system: str
    source_ref: str = ""
    effective_at: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class DecisionCandidate:
    decision_type: str
    title: str
    recommendation: str
    dedupe_key: str
    authority_level: str = "OBSERVE"
    expected_annual_value: float | None = None
    expected_one_time_value: float | None = None
    confidence: float | None = None
    rationale: str = ""
    due_at: str | None = None
    parent_event_id: str | None = None
    action_payload: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
