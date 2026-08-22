from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .models import TaxCandidate, TaxRecord
from .normalize import record_key


def merge_records(records: Iterable[TaxRecord]) -> list[TaxRecord]:
    merged: dict[str, TaxRecord] = {}
    for record in records:
        key = record_key(record.county, record.parcel_id, record.tax_id, record.address, record.city)
        if key not in merged:
            merged[key] = record
            continue
        old = merged[key]
        # A resolved source is authoritative for status when it references the same parcel.
        status = record.status if record.is_resolved else old.status
        if not record.is_resolved and old.is_resolved:
            status = record.status
        years = tuple(sorted(set(old.delinquent_years) | set(record.delinquent_years)))
        source_urls = [u for u in (old.source_url, record.source_url) if u]
        notes = "; ".join(dict.fromkeys(n for n in (old.notes, record.notes) if n))
        merged[key] = replace(
            old,
            parcel_id=record.parcel_id or old.parcel_id,
            tax_id=record.tax_id or old.tax_id,
            address=record.address or old.address,
            city=record.city or old.city,
            state=record.state or old.state,
            zip_code=record.zip_code or old.zip_code,
            owner=record.owner or old.owner,
            delinquent_years=years,
            amount_due=record.amount_due if record.amount_due is not None else old.amount_due,
            appraised_value=record.appraised_value if record.appraised_value is not None else old.appraised_value,
            property_class=record.property_class or old.property_class,
            status=status,
            source_url=" | ".join(dict.fromkeys(source_urls)),
            source_type=record.source_type or old.source_type,
            notes=notes,
        )
    return list(merged.values())


def foreclosure_stage(record: TaxRecord) -> str:
    if record.is_resolved:
        return "Resolved"
    if record.source_type == "foreclosure_exhibit":
        return "Foreclosure filed"
    if record.years_delinquent >= 4:
        return "Foreclosure likely/overdue"
    if record.years_delinquent >= 3:
        return "Foreclosure eligible"
    if record.years_delinquent == 2:
        return "Early warning"
    return "Recent delinquency"


def score_record(record: TaxRecord, *, max_value: float = 130_000) -> int:
    if record.is_resolved:
        return 0
    score = 0
    if record.source_type == "foreclosure_exhibit":
        score += 40
    elif record.years_delinquent >= 3:
        score += 30
    elif record.years_delinquent == 2:
        score += 20
    score += min(record.years_delinquent, 5) * 5
    if record.appraised_value is not None:
        if record.appraised_value <= 100_000:
            score += 15
        elif record.appraised_value <= max_value:
            score += 10
        else:
            score -= 30
        if record.amount_due and record.appraised_value > 0:
            ratio = record.amount_due / record.appraised_value
            if ratio >= 0.10:
                score += 10
            elif ratio >= 0.05:
                score += 5
    else:
        score -= 5
    cls = record.property_class.upper()
    if not cls or any(token in cls for token in ("RES", "SINGLE", "MULTI", "DWELL")):
        score += 5
    if record.address:
        score += 5
    return max(0, min(100, score))


def build_candidates(
    records: Iterable[TaxRecord],
    *,
    min_years: int = 2,
    max_value: float = 130_000,
    include_unknown_value: bool = True,
) -> list[TaxCandidate]:
    candidates: list[TaxCandidate] = []
    for record in merge_records(records):
        if record.is_resolved or record.years_delinquent < min_years:
            continue
        if record.appraised_value is not None and record.appraised_value > max_value:
            continue
        reasons: list[str] = []
        if not record.address:
            reasons.append("missing property address")
        if record.appraised_value is None:
            reasons.append("appraised value not yet verified")
            if not include_unknown_value:
                continue
        if record.source_type == "annual_publication":
            reasons.append("annual-list location should be cross-checked")
        candidates.append(
            TaxCandidate(
                record=record,
                score=score_record(record, max_value=max_value),
                foreclosure_stage=foreclosure_stage(record),
                needs_manual_review=bool(reasons),
                review_reasons=tuple(reasons),
            )
        )
    return sorted(candidates, key=lambda c: (-c.score, c.record.county, c.record.address, c.record.parcel_id))
