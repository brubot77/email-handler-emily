from __future__ import annotations

from datetime import date, datetime

from .economics import EconomicSnapshot, calculate_snapshot
from .models import DecisionCandidate, PropertyIdentity
from .policies import ShadowPolicies


CRITICAL_FACTS = (
    "current_rent",
    "market_rent",
    "lease_end_date",
    "annual_debt_service",
    "loan_balance",
    "estimated_value",
    "annual_operating_expenses",
)


def _parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for parser in (
        lambda: date.fromisoformat(text[:10]),
        lambda: datetime.strptime(text, "%m/%d/%Y").date(),
        lambda: datetime.strptime(text, "%m/%d/%y").date(),
    ):
        try:
            return parser()
        except ValueError:
            pass
    return None


class AssetCeoEngine:
    def __init__(self, policies: ShadowPolicies | None = None) -> None:
        self.policies = policies or ShadowPolicies()

    def evaluate(
        self,
        prop: PropertyIdentity,
        facts: dict[str, object],
        *,
        as_of: date | None = None,
    ) -> tuple[EconomicSnapshot, list[DecisionCandidate]]:
        as_of = as_of or date.today()
        snapshot = calculate_snapshot(facts)
        decisions: list[DecisionCandidate] = []
        period = as_of.strftime("%Y-%m")

        rent_allocation_status = str(facts.get("rent_allocation_status") or "").strip().upper()
        if rent_allocation_status == "REVIEW_REQUIRED":
            grouped_amount = facts.get("rent_roll_group_reported_amount")
            group_label = str(facts.get("rent_allocation_group") or "").strip()
            try:
                grouped_amount_num = float(grouped_amount) if grouped_amount not in (None, "") else None
            except (TypeError, ValueError):
                grouped_amount_num = None

            amount_text = (
                f"${grouped_amount_num:,.0f}/mo "
                if grouped_amount_num is not None
                else "a grouped monthly amount "
            )
            group_text = f" across {group_label}" if group_label else ""
            decisions.append(
                DecisionCandidate(
                    decision_type="RENT_ALLOCATION_REVIEW",
                    title="Resolve grouped rent allocation",
                    recommendation=(
                        f"BLU Tracker Rent Roll reports {amount_text}{group_text}. "
                        "Do not use it as property-level current rent until the amount is allocated to the correct property/unit."
                    ),
                    dedupe_key=f"{prop.property_id}:RENT_ALLOCATION_REVIEW:{period}",
                    authority_level=self.policies.authority_level,
                    confidence=1.0,
                    rationale="Grouped Rent Roll amounts can materially distort rent-gap, NOI, and DSCR analysis.",
                    evidence=[{
                        "evidence_type": "rent_allocation",
                        "source": "blu_tracker_rent_roll",
                        "data": {
                            "rent_allocation_status": rent_allocation_status,
                            "rent_roll_group_reported_amount": grouped_amount_num,
                            "rent_allocation_group": group_label,
                        },
                    }],
                )
            )

        missing = [name for name in CRITICAL_FACTS if facts.get(name) in (None, "")]
        if missing:
            decisions.append(
                DecisionCandidate(
                    decision_type="DATA_COMPLETENESS",
                    title="Complete Property Brain data",
                    recommendation="Load missing operating facts before autonomous economic decisions are enabled: " + ", ".join(missing),
                    dedupe_key=f"{prop.property_id}:DATA_COMPLETENESS:{period}",
                    authority_level=self.policies.authority_level,
                    confidence=1.0,
                    rationale="Asset CEO cannot optimize what it cannot measure.",
                    evidence=[{"evidence_type": "missing_facts", "source": "asset_ceo", "data": {"missing": missing}}],
                )
            )

        lease_end = _parse_date(facts.get("lease_end_date"))
        days_to_lease_end = (lease_end - as_of).days if lease_end else None
        if (
            snapshot.current_rent is not None
            and snapshot.market_rent is not None
            and snapshot.monthly_rent_gap is not None
            and snapshot.monthly_rent_gap > 0
            and days_to_lease_end is not None
            and 0 <= days_to_lease_end <= self.policies.renewal_review_days
        ):
            gap_pct = snapshot.monthly_rent_gap / snapshot.market_rent if snapshot.market_rent else 0.0
            if snapshot.monthly_rent_gap >= self.policies.min_rent_gap_dollars and gap_pct >= self.policies.min_rent_gap_pct:
                annual_opportunity = snapshot.monthly_rent_gap * 12.0
                decisions.append(
                    DecisionCandidate(
                        decision_type="RENT_REVIEW",
                        title="Review renewal rent",
                        recommendation=(
                            f"Current rent ${snapshot.current_rent:,.0f}/mo is ${snapshot.monthly_rent_gap:,.0f}/mo "
                            f"below the ${snapshot.market_rent:,.0f}/mo market estimate. Review renewal pricing before lease expiration."
                        ),
                        dedupe_key=f"{prop.property_id}:RENT_REVIEW:{lease_end.isoformat()}",
                        authority_level=self.policies.authority_level,
                        expected_annual_value=annual_opportunity,
                        confidence=0.80,
                        rationale=f"Lease expires in {days_to_lease_end} days; estimated maximum annual rent gap is ${annual_opportunity:,.0f}.",
                        due_at=lease_end.isoformat(),
                        evidence=[{
                            "evidence_type": "rent_economics", "source": "property_brain",
                            "data": {"current_rent": snapshot.current_rent, "market_rent": snapshot.market_rent, "lease_end_date": lease_end.isoformat()},
                        }],
                    )
                )

        if snapshot.dscr is not None and snapshot.dscr < self.policies.min_dscr:
            decisions.append(
                DecisionCandidate(
                    decision_type="DSCR_RISK",
                    title="DSCR below policy",
                    recommendation=f"Review revenue, operating expenses, and debt structure; DSCR is {snapshot.dscr:.2f} versus {self.policies.min_dscr:.2f} policy minimum.",
                    dedupe_key=f"{prop.property_id}:DSCR_RISK:{period}",
                    authority_level=self.policies.authority_level,
                    confidence=0.95,
                    rationale="Low DSCR reduces debt-service resilience.",
                    evidence=[{"evidence_type": "metric", "source": "asset_ceo", "data": {"dscr": snapshot.dscr}}],
                )
            )

        if (
            snapshot.maintenance_pct_of_rent is not None
            and snapshot.maintenance_pct_of_rent > self.policies.max_maintenance_pct_of_rent
        ):
            excess = None
            if snapshot.maintenance_t12 is not None and snapshot.annual_scheduled_rent is not None:
                excess = snapshot.maintenance_t12 - snapshot.annual_scheduled_rent * self.policies.max_maintenance_pct_of_rent
            decisions.append(
                DecisionCandidate(
                    decision_type="MAINTENANCE_COST_REVIEW",
                    title="Maintenance cost above policy",
                    recommendation=(
                        f"Review T12 maintenance and repeat-repair/vendor patterns; maintenance is "
                        f"{snapshot.maintenance_pct_of_rent:.1%} of scheduled rent."
                    ),
                    dedupe_key=f"{prop.property_id}:MAINTENANCE_COST_REVIEW:{period}",
                    authority_level=self.policies.authority_level,
                    expected_annual_value=max(0.0, excess) if excess is not None else None,
                    confidence=0.90,
                    rationale=f"Policy threshold is {self.policies.max_maintenance_pct_of_rent:.1%} of scheduled rent.",
                    evidence=[{"evidence_type": "metric", "source": "asset_ceo", "data": {"maintenance_pct_of_rent": snapshot.maintenance_pct_of_rent}}],
                )
            )

        oldest_days = facts.get("oldest_open_work_order_days")
        try:
            oldest_days_num = float(oldest_days) if oldest_days not in (None, "") else None
        except (TypeError, ValueError):
            oldest_days_num = None
        if oldest_days_num is not None and oldest_days_num > self.policies.max_open_work_order_days:
            decisions.append(
                DecisionCandidate(
                    decision_type="WORK_ORDER_ESCALATION",
                    title="Open work order needs escalation",
                    recommendation=f"Oldest unresolved work order is {oldest_days_num:.0f} days old; review status and next action.",
                    dedupe_key=f"{prop.property_id}:WORK_ORDER_ESCALATION:{period}",
                    authority_level=self.policies.authority_level,
                    confidence=0.95,
                    rationale=f"Shadow policy threshold is {self.policies.max_open_work_order_days} days.",
                    evidence=[{"evidence_type": "operations", "source": "property_brain", "data": {"oldest_open_work_order_days": oldest_days_num}}],
                )
            )

        return snapshot, decisions
