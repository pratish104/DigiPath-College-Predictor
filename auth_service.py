"""Password hashing and JWT helpers for DigiPath authentication.

Loads .env from the project root at import time so that SECRET_KEY is always
available regardless of how the process is launched (uvicorn, pytest, scripts).
"""

# ── 1. Environment hydration ─────────────────────────────────────────────────
# Must happen before any os.getenv() call in this module.
import pathlib

from dotenv import load_dotenv

_ENV_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)
# ─────────────────────────────────────────────────────────────────────────────

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from passlib.context import CryptContext

log = logging.getLogger("digipath.auth")

# ---------------------------------------------------------------------------
# JWT configuration
# ---------------------------------------------------------------------------

SECRET_KEY: str = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be configured before DigiPath can start.")

if SECRET_KEY == "digipath_super_secret_dev_key_2026":
    raise RuntimeError("SECRET_KEY uses the retired example value; generate a unique secret.")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
)

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored hash.

    Returns False (never raises) for any type / format mismatch so callers
    do not need to handle passlib exceptions.
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (TypeError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    """Hash *password* with the current preferred Argon2 configuration."""
    return pwd_context.hash(password)


# ---------------------------------------------------------------------------
# JWT creation & verification
# ---------------------------------------------------------------------------

def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed, expiring JWT from trusted server-side *data* claims."""
    now = datetime.now(timezone.utc)
    expiration = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    claims = data.copy()
    claims.update({"iat": now, "exp": expiration})
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """Decode a JWT; return ``None`` for expired, malformed, or invalid tokens.

    Callers should treat a ``None`` return as an unauthenticated request.
    """
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        log.info("Rejected expired access token.")
    except JWTError:
        log.warning("Rejected invalid access token.")
    return None


def get_token_payload(token: str) -> Optional[dict[str, Any]]:
    """Return a valid payload only when it contains a non-empty subject claim.

    This is the single entry-point used by route dependencies that need to
    identify the current user from a bearer token.
    """
    payload = decode_access_token(token)
    if (
        not payload
        or not isinstance(payload.get("sub"), str)
        or not payload["sub"].strip()
    ):
        return None
    return payload
