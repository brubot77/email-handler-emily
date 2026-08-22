from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


def _clean_years(years: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted({int(y) for y in years if 1900 <= int(y) <= 2200}))


@dataclass(frozen=True)
class TaxRecord:
    county: str
    parcel_id: str = ""
    tax_id: str = ""
    address: str = ""
    city: str = ""
    state: str = "KS"
    zip_code: str = ""
    owner: str = ""
    delinquent_years: tuple[int, ...] = field(default_factory=tuple)
    amount_due: float | None = None
    appraised_value: float | None = None
    property_class: str = ""
    status: str = "ACTIVE"
    source_url: str = ""
    source_type: str = ""
    notes: str = ""

    # Official parcel/appraisal enrichment fields.
    ain: str = ""
    land_value: float | None = None
    improvement_value: float | None = None
    year_built: int | None = None
    sfla: int | None = None
    living_units: int | None = None
    bedrooms: int | None = None
    full_baths: int | None = None
    half_baths: int | None = None
    value_source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "delinquent_years", _clean_years(self.delinquent_years))
        object.__setattr__(self, "status", (self.status or "ACTIVE").strip().upper())

    @property
    def years_delinquent(self) -> int:
        return len(self.delinquent_years)

    @property
    def is_resolved(self) -> bool:
        return self.status in {"REDEEMED", "DROPPED", "PAID", "RESOLVED"}


@dataclass(frozen=True)
class TaxCandidate:
    record: TaxRecord
    score: int
    foreclosure_stage: str
    needs_manual_review: bool
    review_reasons: tuple[str, ...] = field(default_factory=tuple)
