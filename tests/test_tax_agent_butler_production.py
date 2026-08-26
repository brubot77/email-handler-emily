from __future__ import annotations

import unittest

from app.tax_agent.models import TaxRecord
from app.tax_agent.production import (
    ACQUISITION_TAB,
    SECONDARY_TAB,
    bucket_candidates,
    is_improved_dwelling,
)
from app.tax_agent.core import build_candidates


class ButlerProductionIntegrationTests(unittest.TestCase):
    def _candidate(self, **overrides):
        values = dict(
            county="Butler",
            parcel_id="2120304028011000",
            tax_id="007-663000",
            address="01106 W CARR AVE",
            city="El Dorado",
            delinquent_years=(2021, 2022, 2023, 2024, 2025),
            amount_due=8401.35,
            appraised_value=88500,
            property_class="Residential - R",
            improvement_value=80020,
            source_type="current_tax_verified",
        )
        values.update(overrides)
        return build_candidates(
            [TaxRecord(**values)],
            min_years=2,
            max_value=130000,
            include_unknown_value=False,
        )[0]

    def test_numbered_improved_butler_residence_is_acquisition(self):
        candidate = self._candidate()
        self.assertTrue(is_improved_dwelling(candidate.record))
        buckets = bucket_candidates([candidate])
        self.assertEqual(len(buckets[ACQUISITION_TAB]), 1)
        self.assertEqual(len(buckets[SECONDARY_TAB]), 0)

    def test_unnumbered_butler_residence_is_secondary(self):
        candidate = self._candidate(
            address="NW HWY 196",
            improvement_value=12000,
        )
        self.assertFalse(is_improved_dwelling(candidate.record))
        buckets = bucket_candidates([candidate])
        self.assertEqual(len(buckets[ACQUISITION_TAB]), 0)
        self.assertEqual(len(buckets[SECONDARY_TAB]), 1)

    def test_zero_improvement_butler_residence_is_secondary(self):
        candidate = self._candidate(improvement_value=0)
        self.assertFalse(is_improved_dwelling(candidate.record))
        buckets = bucket_candidates([candidate])
        self.assertEqual(len(buckets[ACQUISITION_TAB]), 0)
        self.assertEqual(len(buckets[SECONDARY_TAB]), 1)

    def test_nonresidential_butler_record_is_not_residential_scope(self):
        record = TaxRecord(
            county="Butler",
            parcel_id="9999999999999999",
            address="100 TEST RD",
            city="Augusta",
            delinquent_years=(2024, 2025),
            appraised_value=50000,
            property_class="Commercial - C",
            improvement_value=40000,
            source_type="current_tax_verified",
        )
        self.assertNotIn(
            "RESIDENTIAL",
            (record.property_class or "").upper(),
        )

    def test_butler_unknown_value_is_excluded_from_production_candidates(self):
        record = TaxRecord(
            county="Butler",
            parcel_id="2910100000018000",
            address="123 TEST ST",
            city="Augusta",
            delinquent_years=(2024, 2025),
            property_class="Residential - R",
            improvement_value=50000,
            source_type="current_tax_verified",
        )
        candidates = build_candidates(
            [record],
            min_years=2,
            max_value=130000,
            include_unknown_value=False,
        )
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
