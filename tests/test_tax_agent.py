from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tax_agent.core import build_candidates, merge_records, score_record
from app.tax_agent.models import TaxRecord
from app.tax_agent.parser import (
    discover_tax_document_links,
    infer_annual_tax_year,
    parse_annual_rows,
    parse_foreclosure_exhibit,
    parse_harvey_foreclosure_notice,
    parse_harvey_news_status,
    parse_years,
)
from app.tax_agent.sources import (
    CountySource,
    _is_stale_annual_url,
    _sitemap_tax_pages,
    discover_county_documents,
)
from app.tax_agent.tracker import write_tracker

EXHIBIT = """
EXHIBIT A
SG-2025-CV-001114
Parcel No. 126
Tax ID No. 122521
Approximate Location: 1018 N WABASH AVE, WICHITA, KS 67214
Delinquent Years: 2020-2023
Redemption Amount: $2,554.25
Current Owner(s): WOODS STANLEY & SHERRI
Parcel No. 127
Tax ID No. 122596
REDEEMED
Parcel No. 130
Tax ID No. 123730
Approximate Location: 921 S TOPEKA AVE 1, WICHITA, KS 67211
Delinquent Years: 2020-2023
Redemption Amount: $4,480.52
Current Owner(s): EDS ENTERPRISES INC
Parcel No. 145
Tax ID No. 125040
DROPPED
"""

HARVEY_NOTICE = """
SHERIFF'S NOTICE OF SALE
CAUSE 9 (Parcel # 08943)
Owner of Record: Ricky Dale Eason
Lots One and Two, Newton, Harvey County, Kansas.
Taxes for the year 2024 and prior years with interest to January 13, 2025 - $3,067.53
CAUSE 11 (Parcel # 00124)
Owners of Record: Jacquelyn C. Havens
Lots Four, Five and Six, Walton, Harvey County, Kansas.
Taxes for the year 2024 and prior years with interest to January 13, 2025 - $11,145.48
"""

class ParserTests(unittest.TestCase):
    def test_parse_year_range_and_single(self):
        self.assertEqual(parse_years("2017-2020, 2023"), (2017, 2018, 2019, 2020, 2023))

    def test_foreclosure_exhibit_parses_and_marks_resolved(self):
        rows = parse_foreclosure_exhibit(EXHIBIT, county="Sedgwick")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].parcel_id, "126")
        self.assertEqual(rows[0].delinquent_years, (2020, 2021, 2022, 2023))
        self.assertEqual(rows[1].status, "REDEEMED")
        self.assertEqual(rows[3].status, "DROPPED")

    def test_document_discovery_stays_on_official_domain(self):
        html = """
        <a href="/media/current/exhibit-a.pdf">Tax Foreclosure Exhibit A</a>
        <a href="https://evil.example/delinquent.pdf">Delinquent real estate</a>
        <a href="/personal-property-delinquent.pdf">Personal Property Delinquent</a>
        """
        links = discover_tax_document_links(html, "https://county.gov/taxes", {"county.gov"})
        self.assertEqual(links, ["https://county.gov/media/current/exhibit-a.pdf"])

    def test_annual_parser_handles_single_space_pdf_text(self):
        text = "ACUNA, DOUGLAS & LAURIE 414 N GREENVALLEY DR ANDOVER, KS 67002 $1507.10"
        rows = parse_annual_rows(text, county="Butler", tax_year=2025)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].address, "414 N GREENVALLEY DR")
        self.assertEqual(rows[0].city, "ANDOVER")
        self.assertEqual(rows[0].amount_due, 1507.10)

    def test_infer_annual_year(self):
        self.assertEqual(infer_annual_tax_year("2019 BUTLER COUNTY REAL ESTATE DELINQUENT TAX LIST"), 2019)

    def test_harvey_notice_and_redeemed_status_merge(self):
        rows = parse_harvey_foreclosure_notice(HARVEY_NOTICE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].parcel_id, "08943")
        self.assertEqual(rows[0].tax_id, "08943")
        self.assertEqual(rows[0].case_id, "CAUSE-9")
        self.assertEqual(rows[0].amount_due, 3067.53)
        self.assertEqual(rows[0].delinquent_years, (2022, 2023, 2024))
        status = parse_harvey_news_status("Since publication, causes 11 have been redeemed.")
        merged = merge_records(rows + status)
        cause11 = next(r for r in merged if r.case_id == "CAUSE-11")
        self.assertTrue(cause11.is_resolved)

class SourceTests(unittest.TestCase):
    def test_butler_2019_publication_is_stale_in_2026(self):
        self.assertTrue(_is_stale_annual_url(
            "https://www.bucoks.gov/DocumentCenter/View/8038/2019-RE-Del-Publication", 2026
        ))
        self.assertFalse(_is_stale_annual_url(
            "https://www.bucoks.gov/DocumentCenter/View/9999/2025-RE-Del-Publication", 2026
        ))

    def test_harvey_sitemap_finds_tax_foreclosure_article(self):
        xml = """<urlset>
        <url><loc>https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22</loc></url>
        <url><loc>https://www.harveycounty.gov/news</loc></url>
        </urlset>"""
        pages = _sitemap_tax_pages(xml, frozenset({"www.harveycounty.gov"}))
        self.assertEqual(pages, ["https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22"])

    def test_harvey_discovery_crawls_sitemap_article_for_attachment(self):
        source = CountySource(
            "Harvey",
            ("https://www.harveycounty.gov/taxes",),
            frozenset({"www.harveycounty.gov"}),
            ("https://www.harveycounty.gov/sitemap.xml",),
        )
        payloads = {
            "https://www.harveycounty.gov/sitemap.xml": b"""<urlset><url><loc>https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22</loc></url></urlset>""",
            "https://www.harveycounty.gov/taxes": b"<html></html>",
            "https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22":
                b'<a href="/media/tax-foreclosure-sale-2026-notice-of-sheriff-sale.pdf">Notice of sheriff sale</a>',
        }
        with patch("app.tax_agent.sources.fetch_bytes", side_effect=lambda url, timeout=30: payloads[url]):
            docs = discover_county_documents(source, current_year=2026)
        self.assertIn("https://www.harveycounty.gov/media/tax-foreclosure-sale-2026-notice-of-sheriff-sale.pdf", docs)
        self.assertIn("https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22", docs)

class CoreTests(unittest.TestCase):
    def test_merge_accumulates_years(self):
        one=TaxRecord(county="Harvey",address="101 Main St",city="Newton",delinquent_years=(2023,))
        two=TaxRecord(county="Harvey",address="101 MAIN STREET",city="Newton",delinquent_years=(2024,))
        rows=merge_records([one,two]); self.assertEqual(rows[0].delinquent_years,(2023,2024))

    def test_resolved_records_are_excluded(self):
        rows=parse_foreclosure_exhibit(EXHIBIT,county="Sedgwick")
        candidates=build_candidates(rows)
        self.assertEqual({c.record.parcel_id for c in candidates},{"126","130"})

    def test_value_limit_and_unknown_handling(self):
        low=TaxRecord(county="Butler",address="1 A ST",city="Andover",delinquent_years=(2023,2024),appraised_value=110000)
        high=TaxRecord(county="Butler",address="2 A ST",city="Andover",delinquent_years=(2023,2024),appraised_value=150000)
        unknown=TaxRecord(county="Butler",address="3 A ST",city="Andover",delinquent_years=(2023,2024))
        c=build_candidates([low,high,unknown])
        self.assertEqual({x.record.address for x in c},{"1 A ST","3 A ST"})

    def test_foreclosure_record_scores_above_two_year_watch(self):
        fore=TaxRecord(county="Sedgwick",parcel_id="1",address="1 A ST",delinquent_years=(2020,2021,2022,2023),source_type="foreclosure_exhibit",appraised_value=90000)
        watch=TaxRecord(county="Sedgwick",parcel_id="2",address="2 A ST",delinquent_years=(2023,2024),source_type="annual_publication",appraised_value=90000)
        self.assertGreater(score_record(fore),score_record(watch))

class TrackerTests(unittest.TestCase):
    def test_manual_columns_survive_refresh(self):
        record=TaxRecord(county="Sedgwick",parcel_id="126",address="1018 N WABASH AVE",city="WICHITA",delinquent_years=(2020,2021,2022,2023),source_type="foreclosure_exhibit",appraised_value=80000)
        candidate=build_candidates([record])[0]
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"tracker.csv"; write_tracker(path,[candidate])
            with path.open(newline="",encoding="utf-8-sig") as f: rows=list(csv.DictReader(f))
            rows[0]["Review Status"]="RESEARCH";rows[0]["Assigned To"]="Billy";rows[0]["Notes"]="Drive-by needed"
            with path.open("w",newline="",encoding="utf-8-sig") as f:
                w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
            write_tracker(path,[candidate])
            with path.open(newline="",encoding="utf-8-sig") as f: refreshed=list(csv.DictReader(f))[0]
            self.assertEqual(refreshed["Review Status"],"RESEARCH")
            self.assertEqual(refreshed["Assigned To"],"Billy")
            self.assertEqual(refreshed["Notes"],"Drive-by needed")


class Phase3QualityTests(unittest.TestCase):
    def test_sedgwick_multiline_location_and_county_only_placeholder(self):
        sample = """
Parcel No. 56
Tax ID No. 00111458
Legal Description: LOT 17 BLOCK 8 LOUIS' 6TH. ADD., Sedgwick County, KS Approximate Location: 344
W 34TH ST S, WICHITA, KS 67217
Delinquent Years: 2021-2024 Redemption Amount: $6,360.02
Current Owners: GIFFORD WAYNE E & MARGARET A
Parcel No. 61
Tax ID No. 00112427
Legal Description: LOTS 42-44 BLOCK H MONTROSE PARK ADD., Sedgwick County, KS Approximate
Location: Sedgwick County, KS
Delinquent Years: 2021-2024 Redemption Amount: $928.12
Current Owners: FUNKHOUSER JERRY B SR
"""
        rows = parse_foreclosure_exhibit(sample, county="Sedgwick")
        p56 = next(r for r in rows if r.parcel_id == "56")
        p61 = next(r for r in rows if r.parcel_id == "61")
        self.assertEqual(p56.address, "344 W 34TH ST S")
        self.assertEqual(p56.city, "WICHITA")
        self.assertEqual(p56.zip_code, "67217")
        self.assertEqual(p56.owner, "GIFFORD WAYNE E & MARGARET A")
        self.assertEqual(p61.address, "")
        self.assertEqual(p61.city, "")

    def test_sedgwick_pdf_redeemed_typo_is_resolved(self):
        sample = """
Parcel No. 3
Tax ID No. 00099697
RDEEMED
"""
        row = parse_foreclosure_exhibit(sample, county="Sedgwick")[0]
        self.assertTrue(row.is_resolved)

    def test_foreclosure_support_links_skip_generic_bidder_and_copyright(self):
        html = """
        <a href="https://experience.arcgis.com/experience/abc123">Story Map</a>
        <a href="/Procurement/BidderRegistration.aspx">Bidder Registration</a>
        <a href="/site/copyright">Copyright Notices</a>
        """
        links = discover_tax_document_links(
            html,
            "https://www.bucoks.gov/502/Tax-Foreclosure-Sale-Information",
            {"www.bucoks.gov", "experience.arcgis.com"},
            parent_is_foreclosure=True,
        )
        self.assertEqual(links, ["https://experience.arcgis.com/experience/abc123"])

    def test_butler_source_trusts_current_story_map_host(self):
        from app.tax_agent.sources import COUNTY_SOURCES
        butler = next(s for s in COUNTY_SOURCES if s.county == "Butler")
        self.assertIn("experience.arcgis.com", butler.allowed_domains)


class Phase4EnrichmentTests(unittest.TestCase):
    def _house_attrs(self):
        return {
            "PIN": "00252968",
            "AIN": "087144200230100200 ",
            "Owner": "PAXSON LINDA GAY",
            "Prop_Addr": "11 N TONJO CT",
            "Prop_Unit": "",
            "Prop_City": "",
            "Prop_zip": "67052",
            "Class": "R",
            "FunctionCD": "1101",
            "FunctionDs": "Single family residence (detached)",
            "LandVal": 49700,
            "ImprVal": 198120,
            "TotVal": 247820,
            "YRBuilt": 1969,
            "SFLA": 1401,
            "LivingUnit": 1,
            "BedRooms": 2,
            "FullBath": 2,
            "HalfBath": 0,
        }

    def test_sedgwick_key_prefers_tax_id_pin(self):
        from app.tax_agent.normalize import record_key
        key = record_key("Sedgwick", "530", "00252968", "11 N TONJO CT", "Goddard")
        self.assertEqual(key, "SEDGWICK|TAXID|00252968")

    def test_pin_enrichment_populates_official_value_and_details(self):
        from app.tax_agent.enrichment import enrich_sedgwick_records
        record = TaxRecord(
            county="Sedgwick",
            parcel_id="530",
            tax_id="00252968",
            address="11 N TONJO CT",
            city="Goddard",
            delinquent_years=(2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024),
            source_type="foreclosure_exhibit",
        )
        calls = []
        def fake_query(where):
            calls.append(where)
            if "PIN IN" in where:
                return [self._house_attrs()]
            return []
        rows, audit = enrich_sedgwick_records([record], query_func=fake_query)
        row = rows[0]
        self.assertEqual(row.appraised_value, 247820)
        self.assertEqual(row.ain, "087144200230100200")
        self.assertEqual(row.year_built, 1969)
        self.assertEqual(row.sfla, 1401)
        self.assertEqual(row.bedrooms, 2)
        self.assertEqual(row.full_baths, 2)
        self.assertEqual(row.value_source, "Sedgwick County GIS TotVal")
        self.assertEqual(audit["pin_matches"], 1)
        self.assertEqual(audit["value_verified"], 1)

    def test_address_fallback_when_pin_has_no_match(self):
        from app.tax_agent.enrichment import enrich_sedgwick_records
        attrs = self._house_attrs()
        record = TaxRecord(
            county="Sedgwick",
            parcel_id="530",
            tax_id="99999999",
            address="11 N TONJO CT",
            city="Goddard",
            delinquent_years=(2021, 2022, 2023, 2024),
        )
        def fake_query(where):
            if "Prop_Addr IN" in where:
                return [attrs]
            return []
        rows, audit = enrich_sedgwick_records([record], query_func=fake_query)
        self.assertEqual(rows[0].tax_id, "00252968")
        self.assertEqual(rows[0].appraised_value, 247820)
        self.assertEqual(audit["address_matches"], 1)

    def test_commercial_highest_best_use_is_nonresidential(self):
        from app.tax_agent.enrichment import enrich_sedgwick_records, is_clearly_nonresidential
        attrs = {
            "PIN": "00595965",
            "AIN": "087119320330300802 ",
            "Owner": "J I LORD PROPERTIES LLC",
            "Prop_Addr": "",
            "Prop_Unit": "",
            "Prop_City": "",
            "Prop_zip": "",
            "Class": "V",
            "FunctionCD": "9950",
            "FunctionDs": "Commercial highest and best use",
            "LandVal": 4400,
            "ImprVal": 0,
            "TotVal": 4400,
            "YRBuilt": None,
            "SFLA": 0,
            "LivingUnit": 0,
            "BedRooms": 0,
            "FullBath": 0,
            "HalfBath": 0,
        }
        record = TaxRecord(county="Sedgwick", tax_id="00595965", delinquent_years=(2021, 2022, 2023, 2024))
        rows, _ = enrich_sedgwick_records([record], query_func=lambda where: [attrs])
        self.assertTrue(is_clearly_nonresidential(rows[0]))

    def test_verified_value_over_cap_is_filtered(self):
        from app.tax_agent.enrichment import enrich_sedgwick_records
        record = TaxRecord(
            county="Sedgwick",
            tax_id="00252968",
            address="11 N TONJO CT",
            delinquent_years=(2021, 2022, 2023, 2024),
            source_type="foreclosure_exhibit",
        )
        rows, _ = enrich_sedgwick_records([record], query_func=lambda where: [self._house_attrs()])
        self.assertEqual(build_candidates(rows, max_value=130000, include_unknown_value=False), [])

    def test_vacant_residential_parcel_is_kept_but_flagged(self):
        from app.tax_agent.enrichment import enrich_sedgwick_records, is_clearly_nonresidential
        attrs = {
            "PIN": "00139705",
            "AIN": "087122100420600200 ",
            "Owner": "VILLASENOR JUAN MANUEL",
            "Prop_Addr": "1656 N POPLAR AVE",
            "Prop_Unit": "",
            "Prop_City": "WICHITA",
            "Prop_zip": "67214",
            "Class": "V",
            "FunctionCD": "9910",
            "FunctionDs": "Residential highest and best use",
            "LandVal": 10100,
            "ImprVal": 0,
            "TotVal": 10100,
            "YRBuilt": None,
            "SFLA": 0,
            "LivingUnit": 0,
            "BedRooms": 0,
            "FullBath": 0,
            "HalfBath": 0,
        }
        record = TaxRecord(
            county="Sedgwick",
            tax_id="00139705",
            address="1656 N POPLAR AVE",
            city="WICHITA",
            delinquent_years=(2019, 2020, 2021, 2022, 2023, 2024),
            source_type="foreclosure_exhibit",
        )
        rows, _ = enrich_sedgwick_records([record], query_func=lambda where: [attrs])
        self.assertFalse(is_clearly_nonresidential(rows[0]))
        candidate = build_candidates(rows, include_unknown_value=False)[0]
        self.assertIn("vacant/unimproved parcel", candidate.review_reasons)

    def test_tracker_contains_enrichment_columns(self):
        from app.tax_agent.tracker import candidate_row
        record = TaxRecord(
            county="Sedgwick",
            tax_id="00139705",
            address="1656 N POPLAR AVE",
            delinquent_years=(2021, 2022, 2023, 2024),
            appraised_value=10100,
            property_class="V | Residential highest and best use",
            ain="087122100420600200",
            land_value=10100,
            improvement_value=0,
            value_source="Sedgwick County GIS TotVal",
        )
        row = candidate_row(build_candidates([record], include_unknown_value=False)[0], 1)
        self.assertEqual(row["AIN"], "087122100420600200")
        self.assertEqual(row["Property Class"], "V | Residential highest and best use")
        self.assertEqual(row["Appraised Value"], "10100.00")
        self.assertEqual(row["Value Source"], "Sedgwick County GIS TotVal")


class Phase5AmountParsingTests(unittest.TestCase):
    def test_sedgwick_inline_redemption_amount_is_parsed(self):
        sample = """
Parcel No. 134
Tax ID No. 00128036
Approximate Location: 1318 E ALINE ST, WICHITA, KS 67211
Delinquent Years: 2021-2024 Redemption Amount: $4,415.27
Current Owners: TEST OWNER
"""
        row = parse_foreclosure_exhibit(sample, county="Sedgwick")[0]
        self.assertEqual(row.amount_due, 4415.27)
        self.assertEqual(row.delinquent_years, (2021, 2022, 2023, 2024))

    def test_missing_redemption_amount_remains_unknown_not_zero(self):
        from app.tax_agent.tracker import candidate_row
        record = TaxRecord(
            county="Sedgwick",
            tax_id="00128036",
            address="1318 E ALINE ST",
            delinquent_years=(2021, 2022, 2023, 2024),
            appraised_value=47160,
            property_class="R | Single family residence (detached)",
            source_type="foreclosure_exhibit",
        )
        row = candidate_row(build_candidates([record], include_unknown_value=False)[0], 1)
        self.assertEqual(row["Amount Due"], "")
        self.assertEqual(row["Tax/Value %"], "")


class Phase6ProductionTests(unittest.TestCase):
    def _candidate(self, **kwargs):
        base = dict(
            county="Sedgwick",
            tax_id="00123456",
            address="100 N TEST ST",
            city="WICHITA",
            delinquent_years=(2021, 2022, 2023, 2024),
            appraised_value=80000,
            property_class="R | Single family residence (detached)",
            improvement_value=60000,
            sfla=1000,
            living_units=1,
            amount_due=4000,
            source_type="foreclosure_exhibit",
        )
        base.update(kwargs)
        return build_candidates([TaxRecord(**base)], include_unknown_value=False)[0]

    def test_production_bucket_primary_secondary_other(self):
        from app.tax_agent.production import (
            ACQUISITION_TAB, SECONDARY_TAB, OTHER_TAB, bucket_candidates
        )
        primary = self._candidate(tax_id="00111111")
        secondary = self._candidate(
            tax_id="00222222",
            property_class="R | Accessory residential support use (garage/shed)",
            sfla=0,
            living_units=0,
        )
        other = self._candidate(
            tax_id="00333333",
            address="",
            property_class="V | Residential highest and best use",
            improvement_value=0,
            sfla=0,
            living_units=0,
        )
        buckets = bucket_candidates([primary, secondary, other])
        self.assertEqual(len(buckets[ACQUISITION_TAB]), 1)
        self.assertEqual(len(buckets[SECONDARY_TAB]), 1)
        self.assertEqual(len(buckets[OTHER_TAB]), 1)

    def test_duplex_is_acquisition_candidate(self):
        from app.tax_agent.production import is_improved_dwelling
        candidate = self._candidate(
            property_class="R | Duplex",
            living_units=2,
            bedrooms=4,
            full_baths=2,
        )
        self.assertTrue(is_improved_dwelling(candidate.record))

    def test_sheet_column_letter_extends_beyond_z(self):
        from app.tax_agent.sheets import a1_col
        self.assertEqual(a1_col(26), "Z")
        self.assertEqual(a1_col(27), "AA")
        self.assertEqual(a1_col(35), "AI")

    def test_google_sheet_values_keep_pin_text_and_money_numeric(self):
        from app.tax_agent.sheets import build_sheet_values
        from app.tax_agent.tracker import HEADERS
        candidate = self._candidate(tax_id="00123456", amount_due=4321.50)
        values = build_sheet_values([candidate])
        row = values[1]
        self.assertEqual(row[HEADERS.index("Tax ID")], "00123456")
        self.assertEqual(row[HEADERS.index("Amount Due")], 4321.50)
        self.assertEqual(row[HEADERS.index("Appraised Value")], 80000)

    def test_google_manual_fields_override_generated_defaults(self):
        from app.tax_agent.sheets import build_sheet_values
        from app.tax_agent.tracker import HEADERS, candidate_row
        candidate = self._candidate()
        key = candidate_row(candidate, 1)["Record Key"]
        manual = {
            key: {
                "Review Status": "RESEARCH",
                "Assigned To": "Billy",
                "Notes": "Drive-by needed",
            }
        }
        row = build_sheet_values([candidate], manual)[1]
        self.assertEqual(row[HEADERS.index("Review Status")], "RESEARCH")
        self.assertEqual(row[HEADERS.index("Assigned To")], "Billy")
        self.assertEqual(row[HEADERS.index("Notes")], "Drive-by needed")


class Phase61DriveFolderTests(unittest.TestCase):
    def test_default_tax_tracker_folder_is_blu_review_docs(self):
        from app.tax_agent.google_runner import DEFAULT_PARENT_FOLDER_ID
        self.assertEqual(
            DEFAULT_PARENT_FOLDER_ID,
            "194YYZgw0gROlsX01LtQGSz-FubP1n2UT",
        )

    def test_parent_move_args_remove_old_parent(self):
        from app.tax_agent.google_runner import _parent_move_args
        add, remove = _parent_move_args(["OLD_PARENT"], "BLU_FOLDER")
        self.assertEqual(add, "BLU_FOLDER")
        self.assertEqual(remove, "OLD_PARENT")

    def test_parent_move_args_no_remove_when_already_only_destination(self):
        from app.tax_agent.google_runner import _parent_move_args
        add, remove = _parent_move_args(["BLU_FOLDER"], "BLU_FOLDER")
        self.assertEqual(add, "BLU_FOLDER")
        self.assertIsNone(remove)


class Phase7AHarveyTests(unittest.TestCase):
    def test_harvey_notice_builds_foreclosure_year_cause_key(self):
        sample = """
IN THE DISTRICT COURT OF HARVEY COUNTY, KANSAS
No. 25-CV-59
SHERIFF'S NOTICE OF SALE
CAUSE 13 (Parcel # 0522)
Owners of Record: Nathan A. Hedrick
Taxes for the year 2024 and prior years with interest to January 13, 2025 - $1,748.49
"""
        row = parse_harvey_foreclosure_notice(sample)[0]
        self.assertEqual(row.case_id, "2025|CAUSE-13")
        self.assertEqual(row.parcel_id, "0522")
        self.assertEqual(row.tax_id, "0522")
        self.assertIn("Court case 25-CV-59", row.notes)

    def test_two_harvey_cause_13_records_do_not_collide(self):
        newer = """
No. 25-CV-59
SHERIFF'S NOTICE OF SALE
CAUSE 13 (Parcel # 0522)
Owners of Record: Nathan A. Hedrick
Taxes for the year 2024 and prior years - $1,748.49
"""
        older = """
No. HV-24-CV-30
SHERIFF'S NOTICE OF SALE
CAUSE 13 (Parcel # 07314)
Owner of Record: Charles Edward McKinney, Jr
Taxes for the year 2025 and prior years - $11,019.13
"""
        rows = merge_records(
            parse_harvey_foreclosure_notice(newer)
            + parse_harvey_foreclosure_notice(older)
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {r.case_id for r in rows},
            {"2025|CAUSE-13", "2024|CAUSE-13"},
        )

    def test_harvey_redemption_only_resolves_same_foreclosure_year(self):
        newer = """
No. 25-CV-59
SHERIFF'S NOTICE OF SALE
CAUSE 13 (Parcel # 0522)
Owners of Record: Nathan A. Hedrick
Taxes for the year 2024 and prior years - $1,748.49
"""
        older = """
No. HV-24-CV-30
SHERIFF'S NOTICE OF SALE
CAUSE 13 (Parcel # 07314)
Owner of Record: Charles Edward McKinney, Jr
Taxes for the year 2025 and prior years - $11,019.13
"""
        status = parse_harvey_news_status(
            "Causes 11, 13 and 25 from the 2025 tax foreclosure have been redeemed."
        )
        rows = merge_records(
            parse_harvey_foreclosure_notice(newer)
            + parse_harvey_foreclosure_notice(older)
            + status
        )
        r2025 = next(r for r in rows if r.case_id == "2025|CAUSE-13")
        r2024 = next(r for r in rows if r.case_id == "2024|CAUSE-13")
        self.assertTrue(r2025.is_resolved)
        self.assertFalse(r2024.is_resolved)

    def test_harvey_record_key_uses_case_identity(self):
        from app.tax_agent.normalize import record_key
        key = record_key(
            "Harvey", "0522", "0522", "", "", "2025|CAUSE-13"
        )
        self.assertEqual(key, "HARVEY|CASE|2025CAUSE13")

    def test_harvey_exact_taxid_enrichment(self):
        from app.tax_agent.harvey import enrich_harvey_records
        record = TaxRecord(
            county="Harvey",
            parcel_id="08943",
            tax_id="08943",
            case_id="2025|CAUSE-9",
            delinquent_years=(2022, 2023, 2024),
            amount_due=3067.53,
            source_type="foreclosure_notice",
        )

        calls = []
        def fake_query(where):
            calls.append(where)
            return [{
                "TaxID": "08943",
                "PIDNO": "0942001008006000",
                "SitusAddre": "224 OLD MAIN ST, Newton, KS  67114",
                "PriOwnerNa": "HARMS FAMILY TRUST",
                "PropertyTy": "Residential",
                "FLV": 6520,
                "FBV": 31480,
                "FTV": 38000,
                "Weblink": "https://example.test",
            }]

        rows, audit = enrich_harvey_records([record], query_func=fake_query)
        row = rows[0]
        self.assertEqual(row.tax_id, "08943")
        self.assertEqual(row.ain, "0942001008006000")
        self.assertEqual(row.address, "224 OLD MAIN ST")
        self.assertEqual(row.city, "Newton")
        self.assertEqual(row.appraised_value, 38000)
        self.assertEqual(row.land_value, 6520)
        self.assertEqual(row.improvement_value, 31480)
        self.assertEqual(row.value_source, "Harvey County GIS FTV")
        self.assertEqual(audit["exact_taxid_matches"], 1)
        self.assertTrue(any("TaxID IN ('08943')" in call for call in calls))

    def test_harvey_ambiguous_short_id_is_not_partial_matched(self):
        from app.tax_agent.harvey import enrich_harvey_records
        record = TaxRecord(
            county="Harvey",
            parcel_id="0522",
            tax_id="0522",
            case_id="2025|CAUSE-13",
            delinquent_years=(2022, 2023, 2024),
            source_type="foreclosure_notice",
        )

        calls = []
        def fake_query(where):
            calls.append(where)
            return []

        rows, audit = enrich_harvey_records([record], query_func=fake_query)
        self.assertIsNone(rows[0].appraised_value)
        self.assertEqual(audit["no_match"], 1)
        self.assertTrue(all("LIKE" not in call.upper() for call in calls))

    def test_harvey_exempt_is_clearly_nonresidential(self):
        from app.tax_agent.enrichment import is_clearly_nonresidential
        record = TaxRecord(county="Harvey", property_class="Exempt")
        self.assertTrue(is_clearly_nonresidential(record))

    def test_harvey_verified_value_cap_applies(self):
        low = TaxRecord(
            county="Harvey",
            parcel_id="08943",
            tax_id="08943",
            case_id="2025|CAUSE-9",
            address="224 OLD MAIN ST",
            delinquent_years=(2022, 2023, 2024),
            appraised_value=38000,
            property_class="Residential",
            source_type="foreclosure_notice",
        )
        high = TaxRecord(
            county="Harvey",
            parcel_id="00124",
            tax_id="00124",
            case_id="2025|CAUSE-11",
            address="320 1ST AVE",
            delinquent_years=(2022, 2023, 2024),
            appraised_value=144230,
            property_class="Residential",
            source_type="foreclosure_notice",
        )
        rows = build_candidates(
            [low, high],
            max_value=130000,
            include_unknown_value=False,
        )
        self.assertEqual([c.record.tax_id for c in rows], ["08943"])

if __name__=="__main__": unittest.main()
