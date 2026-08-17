from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

from openai import OpenAI

from .models import ActiveDeal


SYSTEM_PROMPT = """You are BLU Appraisal Agent, a conservative real-estate valuation analyst.
Your task is to forecast what a competent third-party BANK APPRAISER is likely to conclude, not what an optimistic investor hopes the property is worth.
You must independently research the subject property, recent comparable sales, and current/recent rental comparables using web search.

GENERAL RULES
- Never invent a sale, rent, property characteristic, price, date, distance, source URL, or adjustment.
- Distinguish verified facts from estimates and disclose material conflicts.
- Prefer county/government property records and credible listing/market sources for subject facts.
- Prefer actual closed sales for appraisal comps. AVMs/Zestimates may be supporting context only, never the primary valuation method.
- Prefer recent rental listings with the same property type, bedroom count, neighborhood, and similar size/condition.
- Use a conservative bank-appraisal mindset. Do not simply average asking prices or AVMs.
- If evidence is insufficient, return nullable figures where necessary, lower the confidence, set status to NEEDS REVIEW, and explain why.
- Only include URLs actually found during web research. Never fabricate a URL.

1-4 UNIT RESIDENTIAL APPRAISAL RULES
- Primary approach: sales comparison.
- Prefer comps within about 0.5-1.0 mile, sold within the last 6 months, similar property type/style, similar bedroom/bath count, and roughly +/-20% gross living area.
- Expand distance/time/size only when necessary and explicitly disclose the expansion.
- Analyze price per square foot but do not value solely on price per square foot.
- Consider condition, basement/finished basement, garage, lot, age, updates, and location differences when evidence supports them.
- Flag non-arm's-length, distressed, unusual, or materially dissimilar sales.

5+ UNIT / COMMERCIAL MULTIFAMILY RULES
- Primary approach: income capitalization plus comparable sales / price-per-unit when data permits.
- Research unit count/mix, rents, occupancy, sale comps, price per unit, and market cap-rate evidence when publicly available.
- If T-12/NOI/occupancy/unit-mix data is unavailable, do not pretend precision; use LOW confidence or NEEDS REVIEW as appropriate.

RENT RULES
- Produce both per-unit monthly and total-property monthly ranges when supportable.
- For one door, per-unit and total are the same.
- For multiunit properties, research unit mix where possible. If unit mix is unknown, clearly state the basis and do not overstate confidence.
- Asking rents are not achieved rents; reconcile conservatively.

CONFIDENCE
- HIGH requires multiple strong, recent, close, materially similar comps and reliable subject facts.
- MEDIUM-HIGH is strong but has one modest limitation.
- MEDIUM has usable evidence but meaningful adjustments/expansion or limited rent/sale data.
- MEDIUM-LOW has substantial uncertainty.
- LOW means the result is directional only.
"""


def _nullable_number() -> dict[str, Any]:
    return {"type": ["number", "null"]}


def _nullable_string() -> dict[str, Any]:
    return {"type": ["string", "null"]}


REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "review_date", "status", "needs_review_reasons", "executive_summary",
        "subject", "appraisal", "rent", "risks", "research_sources",
    ],
    "properties": {
        "review_date": {"type": "string"},
        "status": {"type": "string", "enum": ["COMPLETE", "NEEDS REVIEW"]},
        "needs_review_reasons": {"type": "array", "items": {"type": "string"}},
        "executive_summary": {"type": "string"},
        "subject": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "verified_address", "property_type", "doors", "bedrooms", "bathrooms",
                "sqft", "year_built", "lot_size_sqft", "basement", "garage",
                "condition_notes", "data_confidence", "discrepancies",
            ],
            "properties": {
                "verified_address": {"type": "string"},
                "property_type": {"type": "string"},
                "doors": {"type": ["integer", "null"]},
                "bedrooms": _nullable_number(),
                "bathrooms": _nullable_number(),
                "sqft": _nullable_number(),
                "year_built": {"type": ["integer", "null"]},
                "lot_size_sqft": _nullable_number(),
                "basement": _nullable_string(),
                "garage": _nullable_string(),
                "condition_notes": {"type": "string"},
                "data_confidence": {"type": "string", "enum": ["HIGH", "MEDIUM-HIGH", "MEDIUM", "MEDIUM-LOW", "LOW"]},
                "discrepancies": {"type": "array", "items": {"type": "string"}},
            },
        },
        "appraisal": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "valuation_method", "low", "most_likely", "high", "expected_bank_range_low",
                "expected_bank_range_high", "confidence", "confidence_reason", "methodology",
                "adjustments_summary", "reconciliation", "sale_comps",
            ],
            "properties": {
                "valuation_method": {"type": "string"},
                "low": _nullable_number(),
                "most_likely": _nullable_number(),
                "high": _nullable_number(),
                "expected_bank_range_low": _nullable_number(),
                "expected_bank_range_high": _nullable_number(),
                "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM-HIGH", "MEDIUM", "MEDIUM-LOW", "LOW"]},
                "confidence_reason": {"type": "string"},
                "methodology": {"type": "string"},
                "adjustments_summary": {"type": "string"},
                "reconciliation": {"type": "string"},
                "sale_comps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "address", "distance_miles", "sale_date", "sale_price", "sqft",
                            "price_per_sqft", "beds", "baths", "property_type", "relevance",
                            "adjustment_notes", "source_url",
                        ],
                        "properties": {
                            "address": {"type": "string"},
                            "distance_miles": _nullable_number(),
                            "sale_date": _nullable_string(),
                            "sale_price": _nullable_number(),
                            "sqft": _nullable_number(),
                            "price_per_sqft": _nullable_number(),
                            "beds": _nullable_number(),
                            "baths": _nullable_number(),
                            "property_type": _nullable_string(),
                            "relevance": {"type": "string"},
                            "adjustment_notes": {"type": "string"},
                            "source_url": {"type": "string"},
                        },
                    },
                },
            },
        },
        "rent": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "basis", "per_unit_monthly", "total_monthly", "recommended_underwriting_total",
                "confidence", "confidence_reason", "methodology", "reconciliation", "rent_comps",
            ],
            "properties": {
                "basis": {"type": "string"},
                "per_unit_monthly": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["low", "most_likely", "high"],
                    "properties": {
                        "low": _nullable_number(), "most_likely": _nullable_number(), "high": _nullable_number(),
                    },
                },
                "total_monthly": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["low", "most_likely", "high"],
                    "properties": {
                        "low": _nullable_number(), "most_likely": _nullable_number(), "high": _nullable_number(),
                    },
                },
                "recommended_underwriting_total": _nullable_number(),
                "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM-HIGH", "MEDIUM", "MEDIUM-LOW", "LOW"]},
                "confidence_reason": {"type": "string"},
                "methodology": {"type": "string"},
                "reconciliation": {"type": "string"},
                "rent_comps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "address", "listing_status", "asking_rent", "beds", "baths", "sqft",
                            "distance_miles", "relevance", "source_url",
                        ],
                        "properties": {
                            "address": {"type": "string"},
                            "listing_status": {"type": "string"},
                            "asking_rent": _nullable_number(),
                            "beds": _nullable_number(),
                            "baths": _nullable_number(),
                            "sqft": _nullable_number(),
                            "distance_miles": _nullable_number(),
                            "relevance": {"type": "string"},
                            "source_url": {"type": "string"},
                        },
                    },
                },
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "research_sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "url", "supports"],
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "supports": {"type": "string"},
                },
            },
        },
    },
}


def _deal_prompt(deal: ActiveDeal) -> str:
    valuation_mode = "commercial/income-producing multifamily" if (deal.doors or 1) >= 5 else "1-4 unit residential"
    return f"""Research and produce a complete appraisal + rent forecast for this active deal.

SUBJECT FROM BLU ACTIVE DEALS
Address: {deal.address}
City: {deal.city}
State: {deal.state}
Doors: {deal.doors if deal.doors is not None else 'unknown'}
Deal / source: {deal.deal or 'not provided'}
Seller price: {deal.seller_price if deal.seller_price is not None else 'not provided'}
Tracker appraisal estimate: {deal.appraisal_est if deal.appraisal_est is not None else 'not provided'}
Latest offer: {deal.latest_offer if deal.latest_offer is not None else 'not provided'}
Rehab estimate: {deal.rehab_est if deal.rehab_est is not None else 'not provided'}
Offer date: {deal.offer_date or 'not provided'}
Offer status: {deal.offer_status or 'not provided'}
Property notes: {deal.property_notes or 'not provided'}
Valuation mode: {valuation_mode}
Research date: {date.today().isoformat()}

IMPORTANT
- The BLU tracker values are context, not proof of market value or rent.
- Independently verify the subject property before selecting comps.
- Find multiple actual sale comps and multiple rental comps whenever possible.
- State why each comp is relevant and where it is weaker than the subject.
- The appraisal range should forecast a bank appraisal, with the most_likely value as the single number BLU should expect.
- recommended_underwriting_total should be a conservative rent number BLU could use for underwriting; do NOT write it back to the tracker.
- If the property is 5+ units and reliable NOI/unit mix is unavailable, explicitly lower confidence.
- Review_date must be {date.today().isoformat()}.
"""


def _coerce_order(low: Any, likely: Any, high: Any) -> tuple[Any, Any, Any]:
    nums = [v for v in (low, likely, high) if isinstance(v, (int, float))]
    if len(nums) == 3 and not (low <= likely <= high):
        ordered = sorted(nums)
        return ordered[0], ordered[1], ordered[2]
    return low, likely, high


def _post_validate(data: dict[str, Any]) -> dict[str, Any]:
    appraisal = data.get("appraisal", {})
    appraisal["low"], appraisal["most_likely"], appraisal["high"] = _coerce_order(
        appraisal.get("low"), appraisal.get("most_likely"), appraisal.get("high")
    )
    bank_low, _, bank_high = _coerce_order(
        appraisal.get("expected_bank_range_low"),
        appraisal.get("most_likely"),
        appraisal.get("expected_bank_range_high"),
    )
    appraisal["expected_bank_range_low"] = bank_low
    appraisal["expected_bank_range_high"] = bank_high

    rent = data.get("rent", {})
    for bucket_name in ("per_unit_monthly", "total_monthly"):
        bucket = rent.get(bucket_name, {})
        bucket["low"], bucket["most_likely"], bucket["high"] = _coerce_order(
            bucket.get("low"), bucket.get("most_likely"), bucket.get("high")
        )

    sale_count = len(appraisal.get("sale_comps") or [])
    rent_count = len(rent.get("rent_comps") or [])
    review_reasons = list(data.get("needs_review_reasons") or [])

    if sale_count < 2 and appraisal.get("confidence") not in {"LOW", "MEDIUM-LOW"}:
        appraisal["confidence"] = "MEDIUM-LOW"
        review_reasons.append("Fewer than two usable sale comparables were documented.")
    if rent_count < 2 and rent.get("confidence") not in {"LOW", "MEDIUM-LOW"}:
        rent["confidence"] = "MEDIUM-LOW"
        review_reasons.append("Fewer than two usable rental comparables were documented.")

    if appraisal.get("most_likely") is None:
        review_reasons.append("No supportable single-point appraisal forecast was produced.")
    if rent.get("recommended_underwriting_total") is None:
        review_reasons.append("No supportable underwriting rent was produced.")

    if review_reasons:
        data["status"] = "NEEDS REVIEW"
        data["needs_review_reasons"] = list(dict.fromkeys(review_reasons))
    return data


def research_property(deal: ActiveDeal) -> dict[str, Any]:
    client = OpenAI()
    model = os.getenv("APPRAISAL_MODEL", "gpt-5.6")
    effort = os.getenv("APPRAISAL_REASONING_EFFORT", "high")

    response = client.responses.create(
        model=model,
        reasoning={"effort": effort},
        tools=[
            {
                "type": "web_search",
                "search_context_size": "high",
                "user_location": {
                    "type": "approximate",
                    "city": deal.city,
                    "region": deal.state,
                    "country": "US",
                    "timezone": "America/Chicago",
                },
            }
        ],
        instructions=SYSTEM_PROMPT,
        input=_deal_prompt(deal),
        text={
            "format": {
                "type": "json_schema",
                "name": "blu_appraisal_report",
                "strict": True,
                "schema": REPORT_SCHEMA,
            }
        },
        store=False,
    )

    raw = response.output_text
    if not raw:
        raise RuntimeError("OpenAI returned no structured appraisal output")
    data = json.loads(raw)
    return _post_validate(data)
