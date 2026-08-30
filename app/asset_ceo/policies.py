from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShadowPolicies:
    min_rent_gap_dollars: float = 50.0
    min_rent_gap_pct: float = 0.05
    renewal_review_days: int = 120
    min_dscr: float = 1.20
    max_maintenance_pct_of_rent: float = 0.10
    max_open_work_order_days: int = 3
    authority_level: str = "OBSERVE"
