"""Regression checks for the complete, pathway-separated CAP datasets."""

import unittest

import pandas as pd

from data_loader import DataLoader


class AdmissionCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = pd.read_csv("data/fe_2025.csv", dtype=str)
        cls.loader = DataLoader()
        cls.fe = cls.loader.get_combined_cet_data()
        cls.dse = cls.loader.get_combined_diploma_data()

    def test_official_fe_export_keeps_all_row_level_cap_fields(self):
        expected = {"College Code", "Choice Code", "Course Name", "Stage", "Category", "Cutoff Rank", "Percentile", "City"}
        self.assertTrue(expected.issubset(self.source.columns))
        self.assertEqual(len(self.source), 33398)
        self.assertEqual(self.source["College Code"].nunique(), 362)
        self.assertFalse(self.source.duplicated(["College Code", "Choice Code", "Stage", "Category", "Cutoff Rank", "Percentile"]).any())
        self.assertTrue(self.source["College Code"].str.fullmatch(r"\d{5}").all())
        self.assertTrue(self.source["Choice Code"].str.fullmatch(r"\d{10}").all())
        self.assertTrue(self.source["Stage"].isin({"I", "II"}).all())
        self.assertFalse(self.source["Category"].str.fullmatch(r"I|II|III|IV|\d+").any())
        self.assertFalse(pd.to_numeric(self.source["Cutoff Rank"], errors="coerce").isna().any())
        self.assertFalse(pd.to_numeric(self.source["Percentile"], errors="coerce").isna().any())

    def test_loader_retains_complete_2025_fe_coverage(self):
        fe_2025 = self.fe.loc[self.fe["year"].eq(2025)]
        self.assertEqual(len(fe_2025), len(self.source))
        self.assertEqual(fe_2025["college_code"].nunique(), self.source["College Code"].nunique())
        self.assertIn("5g", set(fe_2025["branch"]))

    def test_official_fe_2024_export_keeps_all_row_level_cap_fields(self):
        source_2024 = pd.read_csv("data/fe_2024.csv", dtype=str)
        expected = {"College Code", "Choice Code", "Course Name", "Stage", "Category", "Cutoff Rank", "Percentile", "City"}
        self.assertTrue(expected.issubset(source_2024.columns))
        self.assertEqual(len(source_2024), 30881)
        self.assertEqual(source_2024["College Code"].nunique(), 343)
        self.assertFalse(source_2024.duplicated(["College Code", "Choice Code", "Stage", "Category", "Cutoff Rank", "Percentile"]).any())
        self.assertTrue(source_2024["College Code"].str.fullmatch(r"\d{5}").all())
        self.assertTrue(source_2024["Choice Code"].str.fullmatch(r"\d{10}").all())
        self.assertTrue(source_2024["Stage"].isin({"I", "II"}).all())
        self.assertFalse(source_2024["Category"].str.fullmatch(r"I|II|III|IV|\d+").any())
        self.assertFalse(pd.to_numeric(source_2024["Cutoff Rank"], errors="coerce").isna().any())
        self.assertFalse(pd.to_numeric(source_2024["Percentile"], errors="coerce").isna().any())

    def test_loader_retains_complete_2024_fe_coverage(self):
        source_2024 = pd.read_csv("data/fe_2024.csv", dtype=str)
        fe_2024 = self.fe.loc[self.fe["year"].eq(2024)]
        self.assertEqual(len(fe_2024), len(source_2024))
        self.assertEqual(fe_2024["college_code"].nunique(), source_2024["College Code"].nunique())

    def test_known_navi_mumbai_colleges_are_fe_and_dse_without_mixing(self):
        codes = {"03190", "03197", "03211"}
        fe_known_2025 = self.fe.loc[(self.fe["year"].eq(2025)) & self.fe["college_code"].isin(codes)]
        fe_known_2024 = self.fe.loc[(self.fe["year"].eq(2024)) & self.fe["college_code"].isin(codes)]
        dse_known = self.dse.loc[self.dse["college_code"].isin(codes)]
        self.assertEqual(set(fe_known_2025["college_code"]), codes)
        self.assertEqual(set(fe_known_2025["city"]), {"Navi Mumbai"})
        self.assertEqual(set(fe_known_2024["college_code"]), codes)
        self.assertEqual(set(fe_known_2024["city"]), {"Navi Mumbai"})
        self.assertEqual(set(dse_known["college_code"]), codes)
        self.assertEqual(set(self.fe["exam_type"]), {"CET"})
        self.assertTrue(set(self.dse["exam_type"]).issubset({"DIPLOMA", "DSE"}))
        self.assertNotIn("GOPEN", set(fe_known_2025["seat_code"]))
        self.assertNotIn("GOPEN", set(fe_known_2024["seat_code"]))
        self.assertIn("GOPEN", set(dse_known["seat_code"]))

    def test_dse_keeps_all_percentage_backed_records_by_pathway(self):
        dse_2024_source = pd.read_csv("data/converted_college_data_final.csv", dtype=str)
        dse_2025_source = pd.read_csv("data/CAP_Cutoff_Data.csv", dtype=str)
        dse_2025_valid = dse_2025_source.loc[pd.to_numeric(dse_2025_source["Percent"], errors="coerce").notna()]

        self.assertEqual(len(self.dse.loc[self.dse["year"].eq(2024)]), len(dse_2024_source))
        self.assertEqual(len(self.dse.loc[self.dse["year"].eq(2025)]), len(dse_2025_valid))
        self.assertEqual(self.dse.loc[self.dse["year"].eq(2024), "college_code"].nunique(), dse_2024_source["College Code"].nunique())
        self.assertEqual(self.dse.loc[self.dse["year"].eq(2025), "college_code"].nunique(), dse_2025_valid["College Code"].nunique())


if __name__ == "__main__":
    unittest.main()
