"""Regression checks for CSV-backed CAP category interpretation."""

import unittest
from pathlib import Path

from pydantic import ValidationError

from cet_predictor import (
    CETPredictor,
    DiplomaPredictor,
    PredictRequest,
    REGIONAL_FALLBACK_MIN,
    _match_branch,
    _match_category_or_seat_code,
    _progressively_expand_regions,
)
from data_loader import COL_CITY, COL_COLLEGE_CODE, COL_ROUND, COL_SEAT_CODE, DataLoader


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
        self.assertTrue(all(row["branch"] == "Computer Science and Engineering" for row in response["results"]))
        self.assertTrue({row["city"] for row in response["results"]}.issubset({"Amravati", "Akola", "Wardha", "Nagpur", "Yavatmal"}))

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
        self.assertTrue(all(row["historical_record_count"] == len(row["historical_records"]) for row in response["recommendations"]))
        self.assertEqual(
            sum(row["historical_record_count"] for row in response["recommendations"]),
            response["total_found"],
        )

    def test_supported_reservation_categories_use_data_backed_families_and_open_seats(self):
        expected_family = {
            "OPEN": "OPEN", "SC": "SC", "ST": "ST", "OBC": "OBC", "EWS": "EWS",
            "VJ/DT-NT(A)": "VJ", "NT-B": "NT-B", "NT-C": "NT-C", "NT-D": "NT-D",
            "SBC": "OBC", "SEBC": "SEBC",
        }
        for category, family in expected_family.items():
            with self.subTest(category=category):
                data = self.loader.get_combined_cet_data() if category == "VJ/DT-NT(A)" else self.loader.get_combined_diploma_data()
                matched = _match_category_or_seat_code(data, category)
                self.assertFalse(matched.empty)
                codes = set(matched[COL_SEAT_CODE])
                # Open seats must remain available on merit to every selected
                # reservation family; the raw CAP values are never rewritten.
                self.assertTrue(any(code.startswith(("GOPEN", "LOPEN")) for code in codes))
                if category != "OPEN":
                    self.assertTrue(any(
                        family.replace("-", "") in code.replace("-", "")
                        or (family == "VJ" and "VJ" in code)
                        for code in codes
                    ))

    def test_actual_special_eligibilities_are_independent_of_reservation_family(self):
        for special in ("LADIES", "TFWS", "PWD", "DEFENCE", "ORPHAN", "MINORITY"):
            with self.subTest(special=special):
                data = self.loader.get_combined_cet_data() if special == "TFWS" else self.loader.get_combined_diploma_data()
                matched = _match_category_or_seat_code(data, "SC", eligible_for_ladies=special == "LADIES", special_eligibilities=[special])
                self.assertFalse(matched.empty)
                if special == "LADIES":
                    self.assertTrue(any(code.startswith("L") for code in matched[COL_SEAT_CODE]))
                elif special == "TFWS":
                    self.assertIn("TFWS", set(matched[COL_SEAT_CODE]))
                elif special == "ORPHAN":
                    self.assertTrue({"ORPHAN", "ORP"}.intersection(set(matched[COL_SEAT_CODE])))
                elif special == "MINORITY":
                    self.assertIn("MI", set(matched[COL_SEAT_CODE]))
                else:
                    prefix = "PWD" if special == "PWD" else "DEF"
                    self.assertTrue(any(code.startswith(prefix) for code in matched[COL_SEAT_CODE]))

    def test_multiple_eligibility_dimensions_combine_without_seat_code_substitution(self):
        data = self.loader.get_combined_diploma_data()
        matched = _match_category_or_seat_code(
            data, "SC", eligible_for_ladies=True, special_eligibilities=["PWD"], university_scope="HOME"
        )
        codes = set(matched[COL_SEAT_CODE])
        self.assertIn("LSC", codes)
        self.assertIn("PWDR-SC", codes)
        self.assertNotIn("GOPENO", codes)

    def test_regional_fallback_counts_colleges_before_serialization(self):
        response = self.cet.predict(
            percentile=98, category="SC", branch="Computer Engineering", city="Navi Mumbai", college_type="Any Type"
        )
        self.assertTrue(response["regional_fallback"])
        self.assertEqual(response["total_found"], len(response["results"]))
        self.assertEqual(response["unique_college_count"], len(response["recommendations"]))
        self.assertGreaterEqual(response["unique_college_count"], 15)
        self.assertTrue({row[COL_CITY] for row in response["results"]}.issubset({"Navi Mumbai", "Mumbai", "Thane", "Raigad"}))
        self.assertEqual(
            sum(row["historical_record_count"] for row in response["recommendations"]), response["total_found"]
        )

    def test_city_with_enough_unique_colleges_does_not_expand(self):
        response = self.cet.predict(percentile=98, category="OPEN", branch="Computer Engineering", city="Pune")
        self.assertFalse(response["regional_fallback"])
        self.assertEqual({row[COL_CITY] for row in response["results"]}, {"Pune"})
        self.assertGreaterEqual(response["unique_college_count"], 15)

    def test_backend_counts_match_frontend_card_and_record_collections_for_multiple_queries(self):
        for branch, city in (("Computer Engineering", "Navi Mumbai"), ("Civil Engineering", "Amravati")):
            with self.subTest(branch=branch, city=city):
                response = self.cet.predict(percentile=90, category="SC", branch=branch, city=city)
                self.assertEqual(response["total_found"], len(response["results"]))
                self.assertEqual(response["unique_college_count"], len(response["recommendations"]))

    def test_reported_city_cases_keep_the_expanded_college_set_through_serialization(self):
        cases = (
            (98, "OPEN", "Computer Engineering", "Mumbai"),
            (98, "SC", "Computer Engineering", "Navi Mumbai"),
            (98, "OBC", "Computer Engineering", "Navi Mumbai"),
            (98, "ST", "Computer Engineering", "Navi Mumbai"),
            (90, "SC", "Information Technology", "Mumbai"),
            (80, "OBC", "Electronics & Telecommunication", "Thane"),
            (65, "ST", "Mechanical Engineering", "Pune"),
        )
        data = self.loader.get_combined_cet_data()
        for score, category, branch, city in cases:
            with self.subTest(category=category, branch=branch, city=city):
                eligible = _match_category_or_seat_code(data, category)
                eligible = eligible.loc[_match_branch(eligible["branch"], branch)]
                exact = eligible.loc[eligible[COL_CITY].eq(city)]
                if exact[COL_COLLEGE_CODE].nunique() < REGIONAL_FALLBACK_MIN:
                    expected, expected_cities = _progressively_expand_regions(eligible, city)
                else:
                    expected_cities = [city]
                    expected = exact

                response = self.cet.predict(score, category, branch=branch, city=city, college_type="Any Type")
                recommendation_codes = {row[COL_COLLEGE_CODE] for row in response["recommendations"]}
                self.assertEqual(recommendation_codes, set(expected[COL_COLLEGE_CODE]))
                self.assertEqual(response["expanded_unique_college_count"], len(recommendation_codes))
                self.assertEqual(response["result_cities"], expected_cities)
                self.assertEqual(response["regional_fallback"], len(expected_cities) > 1)

    def test_fallback_stops_when_the_nearest_ring_is_sufficient(self):
        data = self.loader.get_combined_cet_data()
        eligible = _match_category_or_seat_code(data, "SC")
        eligible = eligible.loc[_match_branch(eligible["branch"], "Computer Engineering")]
        first_ring = eligible.loc[eligible[COL_CITY].isin(["Navi Mumbai", "Mumbai", "Thane", "Raigad"])]
        expanded, cities = _progressively_expand_regions(eligible, "Navi Mumbai")
        self.assertGreaterEqual(first_ring[COL_COLLEGE_CODE].nunique(), REGIONAL_FALLBACK_MIN)
        self.assertEqual(expanded[COL_COLLEGE_CODE].nunique(), first_ring[COL_COLLEGE_CODE].nunique())
        self.assertEqual(cities[:4], ["Navi Mumbai", "Mumbai", "Thane", "Raigad"])
        self.assertNotIn("Palghar", cities)
        self.assertNotIn("Pune", cities)

    def test_frontend_cet_endpoint_returns_the_full_recommendation_set(self):
        response = self.cet.predict(98, "OPEN", branch="Computer Engineering", city="Mumbai", college_type="Any Type")
        self.assertTrue(response["regional_fallback"])
        self.assertGreater(response["unique_college_count"], response["exact_city_unique_college_count"])
        self.assertEqual(len(response["recommendations"]), response["expanded_unique_college_count"])
        self.assertEqual(
            {row[COL_COLLEGE_CODE] for row in response["recommendations"]},
            {row[COL_COLLEGE_CODE] for row in response["results"]},
        )
        template = (Path(__file__).resolve().parents[1] / "templates" / "predictor.html").read_text(encoding="utf-8")
        self.assertIn("data.recommendations || data.results", template)
        self.assertIn("historical_status_counts", template)

    def test_higher_cutoffs_are_retained_and_classified_as_dream_records(self):
        response = self.cet.predict(90, "SC", branch="Computer Engineering", city="Navi Mumbai", college_type="Any Type")
        self.assertGreater(len(response["dream_zone"]), 0)
        self.assertEqual(
            len(response["safe_zone"]) + len(response["target_zone"]) + len(response["dream_zone"]),
            response["total_found"],
        )
        self.assertEqual(
            sum(sum(card["historical_status_counts"].values()) for card in response["recommendations"]),
            response["total_found"],
        )
        self.assertTrue(any(
            record["status"] == "DREAM"
            for card in response["recommendations"]
            for record in card["historical_records"]
        ))

    def test_college_cards_cover_every_applicable_college_at_each_score(self):
        for score in (99, 98, 95, 90):
            with self.subTest(score=score):
                response = self.cet.predict(score, "SC", branch="Computer Engineering", city="Navi Mumbai", college_type="Any Type")
                card_count = sum(
                    sum(card["status"] == status for card in response["recommendations"])
                    for status in ("SAFE", "MODERATE", "DREAM")
                )
                self.assertEqual(card_count, response["unique_college_count"])
                self.assertEqual(
                    {record[COL_COLLEGE_CODE] for record in response["results"]},
                    {card[COL_COLLEGE_CODE] for card in response["recommendations"]},
                )
                if score == 90:
                    self.assertEqual(
                        {card["status"] for card in response["recommendations"]},
                        {"SAFE", "MODERATE", "DREAM"},
                    )

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
