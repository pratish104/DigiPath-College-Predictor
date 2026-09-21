"""Regression tests for exports consuming the current predictor payload."""

import io
import unittest

import pandas as pd
from PyPDF2 import PdfReader

from cet_predictor import CETPredictor
from data_loader import DataLoader
from report_generator import generate_csv_report, generate_excel_report, generate_pdf_report


class PredictorExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.predictor = CETPredictor(DataLoader())
        cls.response = cls.predictor.predict(
            98, "SC", branch="All", city="Navi Mumbai", college_type="Any Type"
        )

    def test_current_payload_exports_complete_unique_colleges(self):
        expected_colleges = self.response["unique_college_count"]
        self.assertGreater(expected_colleges, 24)

        csv_bytes, _, _ = generate_csv_report(self.response, {"score": 98, "category": "SC"})
        csv_lines = csv_bytes.decode("utf-8").splitlines()
        csv_frame = pd.read_csv(io.StringIO("\n".join(csv_lines[8:])))
        self.assertEqual(len(csv_frame), expected_colleges)
        self.assertIn("Historical CAP Records", csv_frame.columns)
        self.assertTrue(csv_frame["College Name"].notna().all())

        excel_bytes, _, _ = generate_excel_report(self.response, {"score": 98, "category": "SC"})
        excel_frame = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Recommendations")
        self.assertEqual(len(excel_frame), expected_colleges)
        self.assertIn("Classification", excel_frame.columns)

        pdf_bytes, _, _ = generate_pdf_report(self.response, {"score": 98, "category": "SC"})
        pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
        self.assertIn("College Name", pdf_text)
        self.assertIn(str(self.response["recommendations"][0]["dte_code"]), pdf_text)
        self.assertNotIn("Total Colleges: 0", pdf_text)

    def test_empty_payload_still_generates_valid_empty_reports(self):
        csv_bytes, _, _ = generate_csv_report([], {})
        self.assertIn(b"Rank,DTE Code", csv_bytes)

        excel_bytes, _, _ = generate_excel_report([], {})
        excel_frame = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Recommendations")
        self.assertEqual(len(excel_frame), 0)

        pdf_bytes, _, _ = generate_pdf_report([], {})
        pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
        self.assertIn("College Name", pdf_text)


if __name__ == "__main__":
    unittest.main()
