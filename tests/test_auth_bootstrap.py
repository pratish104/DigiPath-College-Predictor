"""Focused authentication coverage without using the configured application database."""

import asyncio
import os
import secrets
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "unit-test-signing-key-only")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from fastapi import HTTPException, Response

import auth_service
import models
from auth_routes import UserCreate, get_current_admin_user, login, register
from database import Base
from user_service import UserService


def _json_request(payload: bytes) -> Request:
    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/json")],
            "scheme": "http",
            "path": "/api/auth/login",
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        },
        receive,
    )


class AuthenticationBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()

    def tearDown(self) -> None:
        self.session.close()

    def _login(self, email: str, password: str, login_mode: str):
        payload = (
            '{"email":"' + email + '","password":"' + password + '","login_mode":"' + login_mode + '"}'
        ).encode("utf-8")
        response = Response()
        token = asyncio.run(login(_json_request(payload), response, self.session))
        return token, response

    def test_bootstrap_admin_password_is_created_and_synchronized(self):
        email = "admin@digipath.ai"
        initial_password = secrets.token_urlsafe(24)
        rotated_password = secrets.token_urlsafe(24)

        with patch.dict(
            os.environ,
            {
                "BOOTSTRAP_ADMIN_EMAIL": email,
                "BOOTSTRAP_ADMIN_PASSWORD": initial_password,
            },
        ):
            admin = UserService.seed_admin_user(self.session)

            self.assertIsNotNone(admin)
            self.assertEqual(admin.email, email)
            self.assertEqual(admin.role, "ADMIN")
            self.assertTrue(auth_service.verify_password(initial_password, admin.hashed_password))

            os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = rotated_password
            synchronized = UserService.seed_admin_user(self.session)
            self.assertTrue(auth_service.verify_password(rotated_password, synchronized.hashed_password))
            self.assertFalse(auth_service.verify_password(initial_password, synchronized.hashed_password))

            token, response = self._login(email, rotated_password, "admin")
            self.assertTrue(token.is_admin)
            self.assertEqual(token.role, "ADMIN")
            self.assertIn("HttpOnly", response.headers["set-cookie"])

    def test_normal_registration_login_and_role_separation(self):
        password = secrets.token_urlsafe(24)
        email = "normal-user@example.com"
        register(UserCreate(full_name="Normal User", email=email, password=password), self.session)

        normal_user = self.session.query(models.User).filter(models.User.email == email).first()
        self.assertIsNotNone(normal_user)
        self.assertEqual(normal_user.role, "USER")
        self.assertTrue(auth_service.verify_password(password, normal_user.hashed_password))

        token, response = self._login(email, password, "user")
        self.assertFalse(token.is_admin)
        self.assertEqual(token.role, "USER")
        self.assertIn("HttpOnly", response.headers["set-cookie"])

        with self.assertRaises(HTTPException) as denied:
            get_current_admin_user(normal_user)
        self.assertEqual(denied.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
