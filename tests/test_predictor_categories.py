"""Regression checks for CSV-backed CAP category interpretation."""

import unittest

from pydantic import ValidationError

from cet_predictor import CETPredictor, DiplomaPredictor, PredictRequest, _match_category_or_seat_code
from data_loader import COL_ROUND, COL_SEAT_CODE, DataLoader


class PredictorCategoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = DataLoader()
        cls.cet = CETPredictor(cls.loader)
        cls.dse = DiplomaPredictor(cls.loader)

    def test_invalid_score_is_rejected_by_request_schema(self):
        with self.assertRaises(ValidationError):
            PredictRequest(percentile=999, category="OPEN")

    def test_99_percentile_uses_generic_csv_matching(self):
        response = self.cet.predict(percentile=99, category="OPEN")
        self.assertGreater(response["total_found"], 0)
        self.assertEqual(response["candidate_score"], 99.0)
        self.assertTrue({row["seat_code"] for row in response["results"]}.issubset({"GOPENH", "GOPENO", "GOPENS"}))

    def test_sc_ladies_rows_require_the_plain_language_woman_choice(self):
        general = self.cet.predict(percentile=91, category="SC", branch="Civil Engineering", city="Amravati", year=2025)
        women = self.cet.predict(percentile=91, category="LADIES|SC", branch="Civil Engineering", city="Amravati", year=2025)
        self.assertNotIn("LSCH", {row["seat_code"] for row in general["results"]})
        self.assertIn("LSCH", {row["seat_code"] for row in women["results"]})

    def test_year_round_branch_and_city_stay_exact_filters(self):
        response = self.dse.predict(
            percentage=90, category="SC", branch="Computer Science and Engineering", city="Amravati", year=2024, round_name="Stage-I"
        )
        self.assertGreater(response["total_found"], 0)
        self.assertTrue(all(row["year"] == 2024 and row[COL_ROUND] == "Stage-I" for row in response["results"]))
        self.assertTrue(all(row["city"] == "Amravati" and row["branch"] == "Computer Science and Engineering" for row in response["results"]))

    def test_dse_nt_subtypes_remain_distinct(self):
        response = self.dse.predict(percentage=90, category="NT-A")
        codes = {row[COL_SEAT_CODE] for row in response["results"]}
        self.assertIn("GNTA", codes)
        self.assertNotIn("GNTB", codes)

    def test_sbc_uses_the_csv_obc_records(self):
        response = self.cet.predict(percentile=90, category="SBC", branch="Civil Engineering", city="Amravati", year=2024)
        codes = {row[COL_SEAT_CODE] for row in response["results"]}
        self.assertIn("GOBCS", codes)

    def test_special_selection_adds_only_its_own_special_seat_family(self):
        filters = {"branch": "Computer Science and Engineering", "city": "Chhatrapati Sambhajinagar", "year": 2024, "round_name": "Stage-I"}
        pwd = self.dse.predict(percentage=90, category="PWD|SC", **filters)
        defence = self.dse.predict(percentage=90, category="DEFENCE|SC", **filters)
        self.assertIn("PWDR-SC", {row[COL_SEAT_CODE] for row in pwd["results"]})
        self.assertNotIn("DEFR-SC", {row[COL_SEAT_CODE] for row in pwd["results"]})
        self.assertIn("DEFR-SC", {row[COL_SEAT_CODE] for row in defence["results"]})
        self.assertNotIn("PWDR-SC", {row[COL_SEAT_CODE] for row in defence["results"]})

    def test_unique_college_recommendations_do_not_discard_raw_records(self):
        response = self.cet.predict(
            percentile=99, category="OPEN", branch="Computer Science and Engineering", city="Amravati", year=2025
        )
        self.assertLessEqual(response["unique_college_count"], response["total_found"])
        self.assertEqual(response["unique_college_count"], len(response["recommendations"]))
        self.assertEqual(len({row["college_code"] for row in response["recommendations"]}), len(response["recommendations"]))
        self.assertTrue(all("seat_context" in row for row in response["results"]))

    def test_other_special_seats_need_their_own_selection(self):
        fe = self.loader.get_combined_cet_data()
        dse = self.loader.get_combined_diploma_data()
        open_fe = _match_category_or_seat_code(fe, "OPEN")
        tfws = _match_category_or_seat_code(fe, "TFWS|OPEN")
        orphan = _match_category_or_seat_code(fe, "ORPHAN|OPEN")
        minority = _match_category_or_seat_code(dse, "MINORITY|OPEN")
        self.assertNotIn("TFWS", set(open_fe[COL_SEAT_CODE]))
        self.assertIn("TFWS", set(tfws[COL_SEAT_CODE]))
        self.assertIn("ORPHAN", set(orphan[COL_SEAT_CODE]))
        self.assertIn("MI", set(minority[COL_SEAT_CODE]))

    def test_home_and_other_university_scope_stays_exact(self):
        fe = self.loader.get_combined_cet_data()
        home = _match_category_or_seat_code(fe, "OPEN", university_scope="HOME")
        other = _match_category_or_seat_code(fe, "OPEN", university_scope="OTHER")
        self.assertIn("GOPENH", set(home[COL_SEAT_CODE]))
        self.assertNotIn("GOPENO", set(home[COL_SEAT_CODE]))
        self.assertIn("GOPENO", set(other[COL_SEAT_CODE]))
        self.assertNotIn("GOPENH", set(other[COL_SEAT_CODE]))

    def test_exact_nonexistent_filters_return_no_fabricated_result(self):
        response = self.cet.predict(percentile=90, category="OPEN", branch="Not a real engineering branch", city="Amravati")
        self.assertEqual(response["total_found"], 0)


if __name__ == "__main__":
    unittest.main()
