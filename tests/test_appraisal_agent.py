from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.appraisal_agent.google_workspace import canonical_property_key, report_filename
from app.appraisal_agent.models import ActiveDeal
from app.appraisal_agent.report import create_report_docx


class AppraisalAgentTests(unittest.TestCase):
    def test_canonical_key_preserves_direction(self):
        self.assertEqual(
            canonical_property_key("315 SW 2nd Street", "Newton", "KS"),
            "315 sw 2nd|newton|ks",
        )
        self.assertNotEqual(
            canonical_property_key("101 N Main St", "Newton", "KS"),
            canonical_property_key("101 S Main St", "Newton", "KS"),
        )

    def test_report_filename(self):
        deal = ActiveDeal(2, "315 SW 2nd St", "Newton", "KS")
        self.assertEqual(
            report_filename(deal),
            "315 SW 2nd St - Newton KS - Appraisal Review.docx",
        )

    def test_report_generation(self):
        deal = ActiveDeal(
            row_number=2,
            address="315 SW 2nd St",
            city="Newton",
            state="KS",
            deal="Zion",
            doors=1,
            seller_price=74500,
            latest_offer=74500,
        )
        data = {
            "review_date": "2026-08-16",
            "status": "COMPLETE",
            "needs_review_reasons": [],
            "executive_summary": "Test summary.",
            "subject": {
                "verified_address": "315 SW 2nd St, Newton, KS",
                "property_type": "Single-family",
                "doors": 1,
                "bedrooms": 3,
                "bathrooms": 1,
                "sqft": 1100,
                "year_built": 1950,
                "lot_size_sqft": 7000,
                "basement": "Unfinished",
                "garage": "1-car",
                "condition_notes": "Average",
                "data_confidence": "HIGH",
                "discrepancies": [],
            },
            "appraisal": {
                "valuation_method": "Sales comparison",
                "low": 75000,
                "most_likely": 79000,
                "high": 83000,
                "expected_bank_range_low": 77000,
                "expected_bank_range_high": 81000,
                "confidence": "MEDIUM-HIGH",
                "confidence_reason": "Test",
                "methodology": "Test methodology",
                "adjustments_summary": "Test adjustments",
                "reconciliation": "Test reconciliation",
                "sale_comps": [{
                    "address": "123 Test St",
                    "distance_miles": 0.2,
                    "sale_date": "2026-05-01",
                    "sale_price": 80000,
                    "sqft": 1080,
                    "price_per_sqft": 74.07,
                    "beds": 3,
                    "baths": 1,
                    "property_type": "Single-family",
                    "relevance": "Similar",
                    "adjustment_notes": "Minor size difference",
                    "source_url": "https://example.com/sale",
                }],
            },
            "rent": {
                "basis": "Whole-property monthly",
                "per_unit_monthly": {"low": 950, "most_likely": 1025, "high": 1100},
                "total_monthly": {"low": 950, "most_likely": 1025, "high": 1100},
                "recommended_underwriting_total": 1025,
                "confidence": "MEDIUM",
                "confidence_reason": "Test",
                "methodology": "Test methodology",
                "reconciliation": "Test reconciliation",
                "rent_comps": [{
                    "address": "456 Rent St",
                    "listing_status": "Active",
                    "asking_rent": 1050,
                    "beds": 3,
                    "baths": 1,
                    "sqft": 1150,
                    "distance_miles": 0.4,
                    "relevance": "Similar",
                    "source_url": "https://example.com/rent",
                }],
            },
            "risks": ["Test risk"],
            "research_sources": [{"title": "Example", "url": "https://example.com", "supports": "Test"}],
        }
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "test.docx"
            create_report_docx(deal, data, output)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 5000)


if __name__ == "__main__":
    unittest.main()
