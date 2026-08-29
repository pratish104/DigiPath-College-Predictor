"""Password hashing and JWT helpers for DigiPath authentication."""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from passlib.context import CryptContext

log = logging.getLogger("digipath.auth")

_development_secret = "development-only-change-this-secret-before-deployment"
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    log.critical("SECRET_KEY is not configured. Using an unsafe development fallback.")
    SECRET_KEY = _development_secret

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password without exposing hash-format errors to callers."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (TypeError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    """Hash a password with the current preferred Argon2 configuration."""
    return pwd_context.hash(password)


def create_access_token(data: dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed, expiring JWT from trusted server-side claims."""
    now = datetime.now(timezone.utc)
    expiration = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    claims = data.copy()
    claims.update({"iat": now, "exp": expiration})
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """Decode a JWT, returning None for expired, malformed, or invalid tokens."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        log.info("Rejected expired access token.")
    except JWTError:
        log.warning("Rejected invalid access token.")
    return None


def get_token_payload(token: str) -> Optional[dict[str, Any]]:
    """Return a valid payload only when it contains a usable subject claim."""
    payload = decode_access_token(token)
    if not payload or not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
        return None
    return payload
