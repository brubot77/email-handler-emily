from __future__ import annotations

from dataclasses import dataclass


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "complete"}


@dataclass(frozen=True)
class EconomicSnapshot:
    annual_scheduled_rent: float | None = None
    annual_effective_revenue: float | None = None
    annual_operating_expenses: float | None = None
    annual_noi: float | None = None
    annual_debt_service: float | None = None
    dscr: float | None = None
    annual_cash_flow: float | None = None
    estimated_value: float | None = None
    loan_balance: float | None = None
    estimated_equity: float | None = None
    roe: float | None = None
    current_rent: float | None = None
    market_rent: float | None = None
    monthly_rent_gap: float | None = None
    rent_capture_pct: float | None = None
    maintenance_t12: float | None = None
    maintenance_pct_of_rent: float | None = None

    def metric_map(self) -> dict[str, tuple[float, str]]:
        rows: dict[str, tuple[float, str]] = {}
        mapping = {
            "annual_scheduled_rent": (self.annual_scheduled_rent, "USD/year"),
            "annual_effective_revenue": (self.annual_effective_revenue, "USD/year"),
            "annual_operating_expenses": (self.annual_operating_expenses, "USD/year"),
            "annual_noi": (self.annual_noi, "USD/year"),
            "annual_debt_service": (self.annual_debt_service, "USD/year"),
            "dscr": (self.dscr, "ratio"),
            "annual_cash_flow": (self.annual_cash_flow, "USD/year"),
            "estimated_value": (self.estimated_value, "USD"),
            "loan_balance": (self.loan_balance, "USD"),
            "estimated_equity": (self.estimated_equity, "USD"),
            "roe": (self.roe, "ratio"),
            "current_rent": (self.current_rent, "USD/month"),
            "market_rent": (self.market_rent, "USD/month"),
            "monthly_rent_gap": (self.monthly_rent_gap, "USD/month"),
            "rent_capture_pct": (self.rent_capture_pct, "ratio"),
            "maintenance_t12": (self.maintenance_t12, "USD/year"),
            "maintenance_pct_of_rent": (self.maintenance_pct_of_rent, "ratio"),
        }
        for key, (value, unit) in mapping.items():
            if value is not None:
                rows[key] = (float(value), unit)
        return rows


def calculate_snapshot(facts: dict[str, object]) -> EconomicSnapshot:
    current_rent = _num(facts.get("current_rent"))
    market_rent = _num(facts.get("market_rent"))
    vacancy_rate = _num(facts.get("vacancy_rate")) or 0.0
    other_income = _num(facts.get("annual_other_income")) or 0.0

    annual_scheduled_rent = _num(facts.get("annual_scheduled_rent"))
    if annual_scheduled_rent is None and current_rent is not None:
        annual_scheduled_rent = current_rent * 12.0

    annual_effective_revenue = _num(facts.get("annual_effective_revenue"))
    if annual_effective_revenue is None and annual_scheduled_rent is not None:
        annual_effective_revenue = annual_scheduled_rent * (1.0 - max(0.0, vacancy_rate)) + other_income

    # Do not silently treat missing operating-expense categories as zero. BLU
    # Tracker v1.1 initially supplies taxes and insurance, but not a complete
    # maintenance/PM/utilities/other expense picture. Partial expenses must not
    # create false NOI or DSCR precision. Aggregate components only when an
    # upstream source explicitly marks them complete.
    annual_operating_expenses = _num(facts.get("annual_operating_expenses"))
    if annual_operating_expenses is None and _truthy(facts.get("operating_expenses_complete")):
        expense_parts = [
            _num(facts.get("maintenance_t12")),
            _num(facts.get("annual_property_taxes")),
            _num(facts.get("annual_insurance")),
            _num(facts.get("annual_pm_fees")),
            _num(facts.get("annual_utilities")),
            _num(facts.get("annual_other_operating_expenses")),
        ]
        annual_operating_expenses = sum(v or 0.0 for v in expense_parts)

    annual_noi = _num(facts.get("annual_noi"))
    if annual_noi is None and annual_effective_revenue is not None and annual_operating_expenses is not None:
        annual_noi = annual_effective_revenue - annual_operating_expenses

    annual_debt_service = _num(facts.get("annual_debt_service"))
    if annual_debt_service is None:
        monthly_debt_service = _num(facts.get("monthly_debt_service"))
        if monthly_debt_service is not None:
            annual_debt_service = monthly_debt_service * 12.0

    dscr = None
    if annual_noi is not None and annual_debt_service not in (None, 0):
        dscr = annual_noi / annual_debt_service

    annual_cash_flow = None
    if annual_noi is not None:
        annual_cash_flow = annual_noi - (annual_debt_service or 0.0)

    estimated_value = _num(facts.get("estimated_value"))
    loan_balance = _num(facts.get("loan_balance"))
    estimated_equity = None
    if estimated_value is not None and loan_balance is not None:
        estimated_equity = estimated_value - loan_balance

    roe = None
    if annual_cash_flow is not None and estimated_equity not in (None, 0):
        roe = annual_cash_flow / estimated_equity

    monthly_rent_gap = None
    rent_capture_pct = None
    if current_rent is not None and market_rent is not None:
        monthly_rent_gap = market_rent - current_rent
        if market_rent != 0:
            rent_capture_pct = current_rent / market_rent

    maintenance_t12 = _num(facts.get("maintenance_t12"))
    maintenance_pct_of_rent = None
    if maintenance_t12 is not None and annual_scheduled_rent not in (None, 0):
        maintenance_pct_of_rent = maintenance_t12 / annual_scheduled_rent

    return EconomicSnapshot(
        annual_scheduled_rent=annual_scheduled_rent,
        annual_effective_revenue=annual_effective_revenue,
        annual_operating_expenses=annual_operating_expenses,
        annual_noi=annual_noi,
        annual_debt_service=annual_debt_service,
        dscr=dscr,
        annual_cash_flow=annual_cash_flow,
        estimated_value=estimated_value,
        loan_balance=loan_balance,
        estimated_equity=estimated_equity,
        roe=roe,
        current_rent=current_rent,
        market_rent=market_rent,
        monthly_rent_gap=monthly_rent_gap,
        rent_capture_pct=rent_capture_pct,
        maintenance_t12=maintenance_t12,
        maintenance_pct_of_rent=maintenance_pct_of_rent,
    )
