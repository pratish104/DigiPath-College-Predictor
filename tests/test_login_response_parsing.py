import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth_service
import models
from app import app
from database import Base, get_db


class LoginResponseParsingTests(unittest.TestCase):
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
        cls.password = "valid-password-123"
        cls.email = "login-tester@example.com"
        user = models.User(
            full_name="Login Tester",
            email=cls.email,
            hashed_password=auth_service.get_password_hash(cls.password),
            role="USER",
            credits_balance=50,
        )
        session.add(user)
        session.commit()
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

    def test_valid_login_returns_json_token(self):
        response = self.client.post(
            "/api/auth/login",
            json={
                "email": self.email,
                "password": self.password,
                "login_mode": "user",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers["content-type"])
        payload = response.json()
        self.assertEqual(payload["token_type"], "bearer")
        self.assertEqual(payload["email"], self.email)
        self.assertTrue(payload["access_token"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    def test_invalid_login_returns_json_error(self):
        response = self.client.post(
            "/api/auth/login",
            json={
                "email": self.email,
                "password": "wrong-password",
                "login_mode": "user",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertEqual(
            response.json()["detail"],
            "Invalid email or access credentials. Access denied.",
        )

    def test_malformed_login_request_returns_json_error(self):
        response = self.client.post(
            "/api/auth/login",
            content="not-json",
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertEqual(
            response.json()["detail"],
            "Missing credentials: Email/Username and password are required.",
        )

    def test_login_form_uses_safe_response_parser(self):
        login_html = Path("templates/login.html").read_text(encoding="utf-8")
        handle_login = login_html.split("async function handleLogin", 1)[1].split(
            "async function handleRegister", 1
        )[0]

        self.assertIn("async function parseAuthResponse(res)", login_html)
        self.assertIn("const data = await parseAuthResponse(res);", handle_login)
        self.assertNotIn("await res.json()", handle_login)


if __name__ == "__main__":
    unittest.main()
