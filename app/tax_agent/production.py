from __future__ import annotations

from collections import OrderedDict
from typing import Iterable

from .models import TaxCandidate, TaxRecord


ACQUISITION_TAB = "Acquisition Candidates"
SECONDARY_TAB = "Secondary Residential"
OTHER_TAB = "Vacant - Other"
PRODUCTION_TABS = (ACQUISITION_TAB, SECONDARY_TAB, OTHER_TAB)


def is_residential_record(record: TaxRecord) -> bool:
    text = (record.property_class or "").upper()
    return any(token in text for token in ("RESIDENTIAL", "SINGLE FAMILY", "DUPLEX"))


def is_improved_dwelling(record: TaxRecord) -> bool:
    text = (record.property_class or "").upper()
    return (
        is_residential_record(record)
        and bool((record.address or "").strip())
        and (record.improvement_value or 0) > 0
        and (record.sfla or 0) > 0
        and (
            (record.living_units or 0) >= 1
            or "SINGLE FAMILY" in text
            or "DUPLEX" in text
        )
    )


def bucket_candidates(
    candidates: Iterable[TaxCandidate],
) -> "OrderedDict[str, list[TaxCandidate]]":
    buckets: "OrderedDict[str, list[TaxCandidate]]" = OrderedDict(
        (name, []) for name in PRODUCTION_TABS
    )

    for candidate in candidates:
        record = candidate.record
        if is_improved_dwelling(record):
            buckets[ACQUISITION_TAB].append(candidate)
        elif is_residential_record(record) and (record.address or "").strip():
            buckets[SECONDARY_TAB].append(candidate)
        else:
            buckets[OTHER_TAB].append(candidate)

    return buckets
