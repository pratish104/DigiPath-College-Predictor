"""End-to-end checks for the registered predictor export endpoints."""

import io
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from PyPDF2 import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth_service
import models
from app import app
from database import Base, get_db


class PredictorExportEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        cls.Session = sessionmaker(bind=engine)

        session = cls.Session()
        user = models.User(
            full_name="Export Tester",
            email="export-tester@example.com",
            hashed_password=auth_service.get_password_hash("password-123"),
            role="USER",
            credits_balance=50,
        )
        session.add(user)
        session.commit()
        cls.token = auth_service.create_access_token({"sub": user.email})
        session.close()

        def override_get_db():
            session = cls.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_authorized_endpoints_export_complete_current_results(self):
        query = {
            "percentile": 98,
            "category": "SC",
            "branch": "All",
            "city": "Navi Mumbai",
            "college_type": "Any Type",
            "pathway": "fe",
        }
        csv_response = self.client.get("/api/predict/report/csv", params=query, headers=self._headers())
        xlsx_response = self.client.get("/api/predict/report/xlsx", params=query, headers=self._headers())
        pdf_response = self.client.get("/api/predict/report/pdf", params=query, headers=self._headers())

        self.assertEqual(csv_response.status_code, 200)
        csv_lines = csv_response.content.decode("utf-8").splitlines()
        csv_frame = pd.read_csv(io.StringIO("\n".join(csv_lines[8:])))
        self.assertGreater(len(csv_frame), 24)
        self.assertIn("Historical CAP Records", csv_frame.columns)
        csv_codes = csv_frame["DTE Code"].astype(str).str.zfill(5).tolist()
        self.assertIn("03207", csv_codes)

        self.assertEqual(xlsx_response.status_code, 200)
        xlsx_frame = pd.read_excel(io.BytesIO(xlsx_response.content), sheet_name="Recommendations")
        self.assertEqual(len(xlsx_frame), len(csv_frame))
        self.assertIn("Classification", xlsx_frame.columns)

        self.assertEqual(pdf_response.status_code, 200)
        pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_response.content)).pages)
        self.assertIn("College Name", pdf_text)
        self.assertIn("03207", pdf_text)

    def test_empty_result_endpoint_exports_valid_empty_csv(self):
        response = self.client.get(
            "/api/predict/report/csv",
            params={"percentile": 98, "category": "OPEN", "branch": "Not a real branch"},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        lines = response.content.decode("utf-8").splitlines()
        frame = pd.read_csv(io.StringIO("\n".join(lines[8:])))
        self.assertEqual(len(frame), 0)

    def test_unauthorized_endpoint_cannot_export(self):
        response = self.client.get(
            "/api/predict/report/csv",
            params={"percentile": 98, "category": "SC", "branch": "All"},
        )
        self.assertEqual(response.status_code, 401)

    def test_existing_credit_endpoint_remains_unchanged(self):
        with patch("backend_routes.UserService.deduct_download_credit", return_value={
            "success": False,
            "message": "Download limit reached",
        }) as deduct:
            response = self.client.post("/api/user/credits/deduct", headers=self._headers())
        self.assertEqual(response.status_code, 402)
        deduct.assert_called_once()


if __name__ == "__main__":
    unittest.main()
