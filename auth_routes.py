"""Authentication routes and reusable authenticated-user dependencies with Dual-Mode support."""

import json
import logging
import os
from typing import Annotated, Any, Optional

import auth_service
import models
from database import get_db
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session
from user_service import UserService

log = logging.getLogger("digipath.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])

COOKIE_NAME = "access_token"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    # Kept for backwards-compatible clients. Registration is always a
    # least-privilege operation; elevated roles are provisioned out of band.
    role: Optional[str] = Field(default="USER")

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Full name is required")
        return cleaned

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class UserLogin(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    password: str = Field(min_length=1, max_length=128)
    login_mode: Optional[str] = Field(default="user")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_admin: bool = False
    role: str = "USER"
    user_id: Optional[int] = None
    email: Optional[str] = None
    user: Optional[dict[str, Any]] = None


def _extract_bearer_token(authorization: Optional[str], request: Request) -> Optional[str]:
    """Prefer an Authorization bearer token, then accept the browser auth cookie."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
        elif authorization.strip() and not token:
            return authorization.strip()

    if request is not None:
        cookie_value = request.cookies.get(COOKIE_NAME, "").strip()
        if cookie_value:
            scheme, _, token = cookie_value.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            elif cookie_value and not token:
                return cookie_value
    return None


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> models.User:
    """Resolve the authenticated database user from a header or HTTP-only cookie."""
    token = _extract_bearer_token(authorization, request)
    payload = auth_service.get_token_payload(token) if token else None
    email = payload.get("sub") if payload else None
    user = db.query(models.User).filter(models.User.email == email).first() if email else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or not authenticated. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_admin_user(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Require an authenticated account with the ADMIN role."""
    if (current_user.role or "").upper() != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Admin privileges required",
        )
    return current_user


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: UserCreate, db: Session = Depends(get_db)) -> dict[str, str]:
    """Create an account with hashed credentials."""
    email = str(user.email).strip().lower()

    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email identifier already exists.",
        )

    role_to_set = "USER"
    admin_lvl = 0

    new_user = models.User(
        full_name=user.full_name.strip(),
        email=email,
        hashed_password=auth_service.get_password_hash(user.password),
        role=role_to_set,
        admin_level=admin_lvl,
        credits_balance=50,
        profile_settings={
            "roll_number": "",
            "preferred_branch": "",
            "category": "OPEN",
            "target_city": "Mumbai",
            "saved_colleges": [],
            "saved_jobs": [],
            "free_pdf_downloads": 3,
            "credits": 50,
            "is_admin": False,
        },
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except Exception as exc:
        db.rollback()
        log.exception("Registration failed for %s: %s", email, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed. Please verify input data.",
        ) from exc
    return {"message": "User registered successfully", "status": "success"}


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Token:
    """Dual-Mode Login Handler: Accepts JSON & Form-Data for Candidate & Admin authentications."""
    email = ""
    password = ""
    login_mode = "user"
    content_type = request.headers.get("content-type", "").lower()

    # 1. Try parsing JSON body
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                email = str(body.get("email") or body.get("username") or "").strip().lower()
                password = str(body.get("password") or "")
                login_mode = str(body.get("login_mode") or "user").strip().lower()
        except Exception as err:
            log.warning("JSON parse error during login: %s", err)

    # 2. Try parsing form-encoded or multipart body
    if not email or not password:
        try:
            form = await request.form()
            email = str(form.get("username") or form.get("email") or "").strip().lower()
            password = str(form.get("password") or "")
            login_mode = str(form.get("login_mode") or login_mode).strip().lower()
        except Exception:
            pass

    # 3. Fallback raw body inspection
    if not email or not password:
        try:
            raw_bytes = await request.body()
            if raw_bytes:
                raw_text = raw_bytes.decode("utf-8", errors="ignore")
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    email = str(parsed.get("email") or parsed.get("username") or "").strip().lower()
                    password = str(parsed.get("password") or "")
                    login_mode = str(parsed.get("login_mode") or login_mode).strip().lower()
        except Exception:
            pass

    # Admin username shorthand alias
    if email == "admin" or email == "administrator":
        email = "admin@digipath.ai"

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials: Email/Username and password are required.",
        )

    # Synchronize the explicitly configured bootstrap administrator, if any.
    bootstrap_admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    if bootstrap_admin_email and email == bootstrap_admin_email:
        UserService.seed_admin_user(db)

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not auth_service.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or access credentials. Access denied.",
        )

    is_admin_user = (user.role or "").upper() == "ADMIN"

    # If user explicitly selected Admin Terminal mode but is not an admin
    if login_mode == "admin" and not is_admin_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ACCESS_DENIED: The specified credentials do not possess Administrator privileges.",
        )

    access_token = auth_service.create_access_token({"sub": user.email, "role": user.role})

    # Set HTTP-only session cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=f"Bearer {access_token}",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=auth_service.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/"
    )

    user_info = {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "is_admin": is_admin_user,
    }

    return Token(
        access_token=access_token,
        token_type="bearer",
        is_admin=is_admin_user,
        role=user.role,
        user_id=user.id,
        email=user.email,
        user=user_info,
    )


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    """Clear session authentication cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/", httponly=True, secure=COOKIE_SECURE, samesite="lax")
    return {"message": "Successfully logged out", "status": "success"}


@router.get("/me")
def get_current_user_profile(current_user: models.User = Depends(get_current_user)) -> dict[str, Any]:
    """Return active user identity and profile metadata."""
    settings = current_user.profile_settings or {}
    is_admin = (current_user.role or "").upper() == "ADMIN"
    return {
        "id": current_user.id,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "role": current_user.role,
        "is_admin": is_admin,
        "career_interests": current_user.career_interests or [],
        "roll_number": settings.get("roll_number", ""),
        "preferred_branch": settings.get("preferred_branch", ""),
        "category": settings.get("category", "OPEN"),
        "target_city": settings.get("target_city", "Mumbai"),
        "saved_colleges": settings.get("saved_colleges", []),
        "saved_jobs": settings.get("saved_jobs", []),
        "free_pdf_downloads": settings.get("free_pdf_downloads", 9999 if is_admin else 3),
        "credits": settings.get("credits", 9999 if is_admin else 0),
    }
