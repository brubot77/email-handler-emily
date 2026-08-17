from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CONFIDENCE_LEVELS = {"HIGH", "MEDIUM-HIGH", "MEDIUM", "MEDIUM-LOW", "LOW"}


@dataclass
class ActiveDeal:
    row_number: int
    address: str
    city: str
    state: str
    deal: str = ""
    doors: int | None = None
    seller_price: float | None = None
    appraisal_est: float | None = None
    latest_offer: float | None = None
    rehab_est: float | None = None
    offer_date: str = ""
    offer_status: str = ""
    property_notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display_address(self) -> str:
        parts = [self.address.strip(), self.city.strip(), self.state.strip()]
        return ", ".join(part for part in parts if part)


@dataclass
class RunResult:
    scanned: int = 0
    pending: int = 0
    skipped_existing: int = 0
    completed: int = 0
    needs_review: int = 0
    failed: int = 0
    created_reports: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"Scanned: {self.scanned}",
            f"Pending: {self.pending}",
            f"Skipped existing: {self.skipped_existing}",
            f"Completed: {self.completed}",
            f"Needs review: {self.needs_review}",
            f"Failed: {self.failed}",
        ]
        if self.created_reports:
            lines.append("Created reports: " + ", ".join(self.created_reports))
        if self.errors:
            lines.append("Errors: " + " | ".join(self.errors))
        return "\n".join(lines)
