from __future__ import annotations

import unittest

from app.tax_agent.butler import (
    ButlerClient,
    latest_appraisal,
    parse_appraiser_values,
    pid_from_cama,
)


class ButlerPhase9ATests(unittest.TestCase):
    def test_pid_from_18_digit_cic_cama(self):
        self.assertEqual(
            pid_from_cama("205160200200700001"),
            "2051602002007000",
        )

    def test_parse_appraiser_values_uses_official_span_ids(self):
        html = """
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesTaxYear_0">2026</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesClass_0">Residential - R</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesFinalLand_0">8,480</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesFinalBldg_0">80,020</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesFinalTotal_0">88,500</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesTaxYear_1">2025</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesClass_1">Residential - R</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesFinalLand_1">6,950</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesFinalBldg_1">75,950</span>
        <span id="MasterContentPlaceHolder_ApprPropertyValuesDetails_ResultsRepeater_ValuesFinalTotal_1">82,900</span>
        """
        rows = parse_appraiser_values(html)
        self.assertEqual(len(rows), 2)
        latest = latest_appraisal(html)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.tax_year, 2026)
        self.assertEqual(latest.total_value, 88500)
        self.assertEqual(latest.land_value, 8480)
        self.assertEqual(latest.improvement_value, 80020)
        self.assertEqual(latest.property_class, "Residential - R")

    def test_result_parser_unions_current_and_history_links(self):
        html = """
        <a href="../Current_Tax/current_tax.aspx?_CamaNumber=212030402801100001&amp;_TaxUnit=007&amp;_TaxParcel=663000&amp;_StreetNumber=1106&amp;_StreetDirection=W&amp;_StreetName=CARR&amp;_ZipCode=67042&amp;_OwnerName1=SEUSER">Current Taxes</a>
        <a href="../Tax_History/tax_history.aspx?_CamaNumber=212030402801100001&amp;_TaxUnit=007&amp;_TaxParcel=663000&amp;_StreetNumber=1106&amp;_StreetDirection=W&amp;_StreetName=CARR&amp;_ZipCode=67042&amp;_OwnerName1=SEUSER">Tax History</a>
        """
        rows = ButlerClient.parse_result_statements(
            html,
            "https://portals.bucoks.com/taxportal/tax/Search/search_tax_results.aspx",
        )
        self.assertEqual(set(rows), {"2120304028011000"})
        row = rows["2120304028011000"]
        self.assertEqual(row.tax_id, "007-663000")
        self.assertEqual(row.address, "1106 W CARR")
        self.assertTrue(row.current_tax_url)
        self.assertTrue(row.tax_history_url)

    def test_pagination_parser_reads_more_block(self):
        html = """
        <a href="search_tax_results.aspx?&amp;Page=20&amp;InitialPage=1&amp;InitialRecord=380">20</a>
        <a href="search_tax_results.aspx?&amp;Page=21&amp;InitialPage=21&amp;InitialRecord=400">More...</a>
        """
        pages = ButlerClient.pagination_links(
            html,
            "https://portals.bucoks.com/taxportal/tax/Search/search_tax_results.aspx",
        )
        self.assertEqual(set(pages), {20, 21})
        self.assertIn("InitialRecord=400", pages[21])


if __name__ == "__main__":
    unittest.main()
