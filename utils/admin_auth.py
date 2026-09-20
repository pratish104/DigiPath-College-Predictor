"""DigiPath Admin Role-Based Access Control (RBAC) Dependency.

Admin Hierarchy Levels:
  0 = Standard Candidate / User
  1 = Support Admin (Limited access, max +300 credits grant per transaction)
  2 = Super Admin / System Root (Full access, unlimited credit grants, error resolution)
"""

from __future__ import annotations

import logging
from typing import Annotated, Callable, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

import auth_service
from database import get_db
import models

log = logging.getLogger("digipath.admin_auth")
COOKIE_NAME = "access_token"


def _extract_token(request: Request, authorization: Optional[str] = None) -> Optional[str]:
    """Resolve JWT token from Authorization header or HTTP-only session cookie."""
    if authorization:
        scheme, _, bearer_token = authorization.partition(" ")
        if scheme.lower() == "bearer" and bearer_token.strip():
            return bearer_token.strip()
        elif authorization.strip() and not bearer_token:
            return authorization.strip()

    if request is not None:
        cookie_val = request.cookies.get(COOKIE_NAME, "").strip()
        if cookie_val:
            scheme, _, cookie_token = cookie_val.partition(" ")
            if scheme.lower() == "bearer" and cookie_token.strip():
                return cookie_token.strip()
            elif cookie_val and not cookie_token:
                return cookie_val

    return None


def get_authenticated_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> models.User:
    """Resolve active database user from token; raises 401 if missing or expired."""
    token = _extract_token(request, authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AUTHENTICATION_REQUIRED: Session expired or missing credentials. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = auth_service.get_token_payload(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_TOKEN: Authentication token is malformed or signature expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    email = str(payload.get("sub")).strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="USER_NOT_FOUND: Account associated with session does not exist.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_admin_level(min_level: int) -> Callable:
    """
    Dependency factory that enforces a minimum admin privileges tier.
    
    Levels:
      0 = User
      1 = Support Admin
      2 = Super Admin
      
    If user.admin_level < min_level:
        raises HTTPException(403, detail="ACCESS_DENIED: Insufficient Admin Privileges")
    """
    async def admin_level_dependency(
        request: Request,
        db: Session = Depends(get_db),
        authorization: Annotated[Optional[str], Header()] = None,
    ) -> models.User:
        user = get_authenticated_user(request, db, authorization)
        
        # Calculate effective admin level from admin_level field or role string
        user_role = (user.role or "").upper()
        current_level = int(user.admin_level or 0)
        
        if user_role in ("SUPER_ADMIN", "ADMIN"):
            current_level = max(current_level, 2)
        elif user_role == "SUPPORT_ADMIN":
            current_level = max(current_level, 1)

        # Synchronize model if out of sync
        if user.admin_level != current_level:
            user.admin_level = current_level
            try:
                db.commit()
            except Exception:
                db.rollback()

        if current_level < min_level:
            log.warning(
                "Access denied for user %s: required admin level %d, has %d (Role: %s)",
                user.email, min_level, current_level, user.role
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ACCESS_DENIED: Insufficient Admin Privileges"
            )

        return user

    return admin_level_dependency
