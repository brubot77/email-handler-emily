from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.asset_ceo.addressing import canonical_property_key
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


if __name__ == "__main__":
    unittest.main()
