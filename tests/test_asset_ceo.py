from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.asset_ceo.addressing import canonical_property_key
from app.asset_ceo.blu_tracker import parse_blu_tracker_rows, sync_blu_tracker_facts
from app.asset_ceo.economics import calculate_snapshot
from app.asset_ceo.engine import AssetCeoEngine
from app.asset_ceo.models import DecisionCandidate, FactInput
from app.asset_ceo.sources import SourceProperty, sync_source_properties
from app.asset_ceo.store import AssetCeoStore


class AssetCeoTests(unittest.TestCase):
    def test_asset_ceo_key_preserves_direction(self):
        self.assertNotEqual(
            canonical_property_key("101 N Main St", "Newton", "KS"),
            canonical_property_key("101 S Main St", "Newton", "KS"),
        )

    def test_economics(self):
        snap = calculate_snapshot({
            "current_rent": 1500,
            "market_rent": 1600,
            "vacancy_rate": 0.05,
            "annual_operating_expenses": 5000,
            "monthly_debt_service": 800,
            "estimated_value": 180000,
            "loan_balance": 100000,
            "maintenance_t12": 1800,
        })
        self.assertAlmostEqual(snap.annual_scheduled_rent, 18000)
        self.assertAlmostEqual(snap.annual_effective_revenue, 17100)
        self.assertAlmostEqual(snap.annual_noi, 12100)
        self.assertAlmostEqual(snap.annual_debt_service, 9600)
        self.assertAlmostEqual(snap.dscr, 12100 / 9600)
        self.assertAlmostEqual(snap.estimated_equity, 80000)
        self.assertAlmostEqual(snap.monthly_rent_gap, 100)
        self.assertAlmostEqual(snap.rent_capture_pct, 1500 / 1600)
        self.assertAlmostEqual(snap.maintenance_pct_of_rent, 0.10)

    def test_partial_expenses_do_not_create_false_noi_or_dscr(self):
        snap = calculate_snapshot({
            "current_rent": 1000,
            "annual_property_taxes": 1200,
            "annual_insurance": 800,
            "monthly_debt_service": 500,
        })
        self.assertIsNone(snap.annual_operating_expenses)
        self.assertIsNone(snap.annual_noi)
        self.assertIsNone(snap.dscr)
        self.assertAlmostEqual(snap.annual_debt_service, 6000)

    def test_sync_is_idempotent_and_facts_append_only(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "asset.db"
            with AssetCeoStore(db) as store:
                store.initialize_schema()
                rec = SourceProperty(
                    address="315 SW 2nd St", city="Newton", state="KS", zip_code="67114",
                    llc="BLU2", active=True, source_system="test", source_ref="morgan-key",
                    facts=(FactInput("property_folder_id", "abc", "test", "morgan-key"),),
                )
                first = sync_source_properties(store, [rec])
                second = sync_source_properties(store, [rec])
                self.assertEqual(first["properties_upserted"], 1)
                self.assertEqual(first["facts_inserted"], 1)
                self.assertEqual(second["properties_upserted"], 1)
                self.assertEqual(second["facts_inserted"], 0)
                self.assertEqual(len(store.list_properties()), 1)

    def test_decision_deduplication(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "asset.db"
            with AssetCeoStore(db) as store:
                store.initialize_schema()
                prop = store.upsert_property(canonical_key="315 sw 2nd|newton|ks", address="315 SW 2nd St", city="Newton", state="KS")
                candidate = DecisionCandidate(
                    decision_type="TEST", title="Test", recommendation="Do thing", dedupe_key="same-key"
                )
                self.assertIsNotNone(store.create_decision(prop.property_id, candidate))
                self.assertIsNone(store.create_decision(prop.property_id, candidate))
                self.assertEqual(len(store.open_decisions(prop.property_id)), 1)

    def test_shadow_engine_creates_rent_review(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "asset.db"
            with AssetCeoStore(db) as store:
                store.initialize_schema()
                prop = store.upsert_property(canonical_key="100 n test|wichita|ks", address="100 N Test", city="Wichita", state="KS")
                facts = {
                    "current_rent": 1200,
                    "market_rent": 1325,
                    "lease_end_date": "2026-10-15",
                    "annual_debt_service": 9000,
                    "loan_balance": 100000,
                    "estimated_value": 160000,
                    "annual_operating_expenses": 4500,
                }
                _, decisions = AssetCeoEngine().evaluate(prop, facts, as_of=date(2026, 8, 30))
                kinds = {d.decision_type for d in decisions}
                self.assertIn("RENT_REVIEW", kinds)

    @staticmethod
    def _blu_tracker_fixture():
        address_rows = [
            [
                "Address", "City", "ST", "LLC", "Doors", "Deal Name", "Refi Group",
                "Purchase Date", " Purchase Price ", " Monthly Tax ", " Orig. Appr. ",
                " Ori. Loan Amt. ", "Insurance Value", " Forecast Rent ", "Mortgage Pmt",
            ],
            [
                "1012 n green", "Wichita", "KS", "BLU1", "1", "Lighthouse", "Lighthouse",
                "03-Dec-2024", "", "$25.55", "$40,000.00", "$26,409.09", "$26.17",
                "$625.00", "$279.57",
            ],
        ]
        rent_rows = [
            ["", "Rent Roll"],
            ["Property Address", "2026-05-13 thru 2026-06-12"],
            ["1012 N Green St", "$600.00"],
        ]
        insurance_rows = [
            ["LLC", "Policy / Section", "Property Address", "Monthly Premium", "Annual Premium", "Coverage"],
            ["BLU1", "Property", "1012 N Green St", "$26.17", "$314.00", "$26,500"],
        ]
        return address_rows, rent_rows, insurance_rows

    def test_blu_tracker_mapping_uses_forecast_rent_and_orig_appraisal(self):
        address_rows, rent_rows, insurance_rows = self._blu_tracker_fixture()
        parsed = parse_blu_tracker_rows(
            address_rows, rent_rows, insurance_rows, spreadsheet_id="test-sheet"
        )
        self.assertEqual(len(parsed.records), 1)
        record = parsed.records[0]
        facts = {fact.fact_name: fact.value for fact in record.facts}

        # Explicit BLU policy: these two fields come from Address Data, not Operly.
        self.assertEqual(facts["market_rent"], 625.0)
        self.assertEqual(facts["estimated_value"], 40000.0)

        self.assertEqual(facts["current_rent"], 600.0)
        self.assertAlmostEqual(facts["annual_debt_service"], 279.57 * 12)
        self.assertAlmostEqual(facts["annual_property_taxes"], 25.55 * 12)
        self.assertEqual(facts["annual_insurance"], 314.0)
        self.assertEqual(facts["original_loan_amount"], 26409.09)
        self.assertNotIn("loan_balance", facts)
        self.assertEqual(parsed.unmatched_rent_addresses, ())
        self.assertEqual(parsed.unmatched_insurance_addresses, ())

    def test_blu_tracker_sync_is_idempotent(self):
        address_rows, rent_rows, insurance_rows = self._blu_tracker_fixture()
        parsed = parse_blu_tracker_rows(address_rows, rent_rows, insurance_rows, spreadsheet_id="test-sheet")

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "asset.db"
            with AssetCeoStore(db) as store:
                store.initialize_schema()
                prop = store.upsert_property(
                    canonical_key=canonical_property_key("1012 N Green St", "Wichita", "KS"),
                    address="1012 N Green St",
                    city="Wichita",
                    state="KS",
                    llc="BLU1",
                )
                first = sync_blu_tracker_facts(store, parsed.records)
                second = sync_blu_tracker_facts(store, parsed.records)

                self.assertEqual(first["properties_matched"], 1)
                self.assertGreater(first["facts_inserted"], 0)
                self.assertEqual(second["facts_inserted"], 0)

                latest = store.latest_facts(prop.property_id)
                self.assertEqual(latest["market_rent"], 625.0)
                self.assertEqual(latest["estimated_value"], 40000.0)
                self.assertEqual(latest["current_rent"], 600.0)
                self.assertNotIn("loan_balance", latest)

    def test_grouped_rent_pair_is_withheld_from_current_rent(self):
        address_rows = [
            [
                "Address", "City", "ST", "LLC", "Doors", "Deal Name", "Refi Group",
                "Purchase Date", " Purchase Price ", " Monthly Tax ", " Orig. Appr. ",
                " Ori. Loan Amt. ", "Insurance Value", " Forecast Rent ", "Mortgage Pmt",
            ],
            [
                "1248 N Volutsia", "Wichita", "KS", "BLU1", "1", "Lighthouse", "Lighthouse",
                "03-Dec-2024", "", "$76.20", "$75,000", "$49,517.05", "$64.59",
                "$742.50", "$332.12",
            ],
            [
                "1250 N Volutsia", "Wichita", "KS", "BLU1", "1", "Lighthouse", "Lighthouse",
                "03-Dec-2024", "", "$76.17", "$75,000", "$49,517.05", "$0.00",
                "$742.50", "$332.12",
            ],
        ]
        rent_rows = [
            ["", "Rent Roll"],
            ["Property Address", "2026-05-13 thru 2026-06-12"],
            ["1248 N Volutsia", "$2,024.00"],
        ]
        insurance_rows = [
            ["LLC", "Policy / Section", "Property Address", "Monthly Premium", "Annual Premium", "Coverage"],
            ["BLU1", "Property", "1248-1250 N Volutsia", "$64.59", "$775.00", "$99,100"],
        ]

        parsed = parse_blu_tracker_rows(
            address_rows, rent_rows, insurance_rows, spreadsheet_id="test-sheet"
        )
        by_address = {record.address: record for record in parsed.records}

        for address in ("1248 N Volutsia", "1250 N Volutsia"):
            facts = {fact.fact_name: fact.value for fact in by_address[address].facts}
            self.assertNotIn("current_rent", facts)
            self.assertEqual(facts["rent_allocation_status"], "REVIEW_REQUIRED")
            self.assertEqual(facts["rent_roll_group_reported_amount"], 2024.0)
            self.assertIn("1248 N Volutsia", facts["rent_allocation_group"])
            self.assertIn("1250 N Volutsia", facts["rent_allocation_group"])

        self.assertEqual(parsed.rent_allocation_review_addresses, ("1248 N Volutsia",))
        self.assertEqual(parsed.unmatched_insurance_addresses, ("1248-1250 N Volutsia",))

    def test_rent_roll_total_is_ignored_not_unmatched(self):
        address_rows, rent_rows, insurance_rows = self._blu_tracker_fixture()
        rent_rows.append(["Total Rent", "$29,284.00"])

        parsed = parse_blu_tracker_rows(
            address_rows, rent_rows, insurance_rows, spreadsheet_id="test-sheet"
        )

        self.assertEqual(parsed.unmatched_rent_addresses, ())
        self.assertEqual(parsed.ignored_rent_summary_rows, ("Total Rent",))

    def test_shadow_engine_creates_rent_allocation_review(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "asset.db"
            with AssetCeoStore(db) as store:
                store.initialize_schema()
                prop = store.upsert_property(
                    canonical_key="1248 n volutsia|wichita|ks",
                    address="1248 N Volutsia",
                    city="Wichita",
                    state="KS",
                )
                facts = {
                    "market_rent": 742.50,
                    "estimated_value": 75000,
                    "rent_allocation_status": "REVIEW_REQUIRED",
                    "rent_roll_group_reported_amount": 2024,
                    "rent_allocation_group": "1248 N Volutsia | 1250 N Volutsia",
                }
                _, decisions = AssetCeoEngine().evaluate(
                    prop, facts, as_of=date(2026, 8, 30)
                )
                kinds = {decision.decision_type for decision in decisions}
                self.assertIn("RENT_ALLOCATION_REVIEW", kinds)


if __name__ == "__main__":
    unittest.main()
