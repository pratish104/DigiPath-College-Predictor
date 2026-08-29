"""Authentication routes and reusable authenticated-user dependencies."""

import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

import auth_service
import models
from database import get_db

router = APIRouter(prefix="/auth", tags=["Authentication"])

COOKIE_NAME = "access_token"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").strip().lower() in {"1", "true", "yes"}


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

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
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class Token(BaseModel):
    access_token: str
    token_type: str


def _extract_bearer_token(authorization: Optional[str], request: Request) -> Optional[str]:
    """Prefer an Authorization bearer token, then accept the browser auth cookie."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()

    cookie_value = request.cookies.get(COOKIE_NAME, "").strip()
    scheme, _, token = cookie_value.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
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
    """Create an ordinary user account; clients cannot select a privileged role."""
    email = str(user.email).strip().lower()
    new_user = models.User(
        full_name=user.full_name.strip(),
        email=email,
        hashed_password=auth_service.get_password_hash(user.password),
        role="USER",
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed or email already exists",
        ) from exc
    return {"message": "User registered successfully"}


@router.post("/login", response_model=Token)
def login(user_data: UserLogin, response: Response, db: Session = Depends(get_db)) -> Token:
    """Authenticate a user and return/store a short-lived bearer token."""
    email = str(user_data.email).strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not auth_service.verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = auth_service.create_access_token({"sub": user.email, "role": user.role})
    response.set_cookie(
        key=COOKIE_NAME,
        value=f"Bearer {access_token}",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=auth_service.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return Token(access_token=access_token, token_type="bearer")


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(key=COOKIE_NAME, path="/", httponly=True, secure=COOKIE_SECURE, samesite="lax")
    return {"message": "Successfully logged out"}


@router.get("/me")
def get_current_user_profile(current_user: models.User = Depends(get_current_user)) -> dict[str, object]:
    return {
        "id": current_user.id,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "role": current_user.role,
        "career_interests": current_user.career_interests or [],
    }
