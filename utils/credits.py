"""DigiPath Credit Wallet System.

Atomic credit deduction and grant management for DigiPath features.
Configurable feature costs:
  - Predictor: 5 credits
  - AI Chat: 2 credits
  - Resume ATS: 10 credits
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

import models

log = logging.getLogger("digipath.credits")

# ── Configurable Feature Costs ────────────────────────────────────────────────
COST_PREDICTOR: int = 5
COST_AI_CHAT: int = 2
COST_RESUME_ATS: int = 10


def get_user_credits(db: Session, user_id: int) -> int:
    """Retrieve the current credit balance of a user."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="USER_NOT_FOUND: Candidate account does not exist."
        )
    return int(user.credits_balance or 0)


def deduct_user_credits(db: Session, user_id: int, cost: int) -> bool:
    """
    Atomically deduct credits from a user's wallet.
    
    If user.credits_balance < cost:
        raises HTTPException(402, detail="INSUFFICIENT_CREDITS: Upgrade to Premium")
        
    Commits session atomically and returns True upon successful deduction.
    Super Admins and Admins bypass credit deduction.
    """
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="USER_NOT_FOUND: Candidate account does not exist."
        )

    # Super Admins and Admins have unlimited access
    if (user.role or "").upper() in ("ADMIN", "SUPER_ADMIN") or (user.admin_level or 0) >= 2:
        log.info("Admin bypass for credit deduction: user_id=%d, cost=%d", user_id, cost)
        return True

    current_balance = int(user.credits_balance if user.credits_balance is not None else 0)
    
    if current_balance < cost:
        log.warning(
            "Insufficient credits: user_id=%d, balance=%d, required=%d",
            user_id, current_balance, cost
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="INSUFFICIENT_CREDITS: Upgrade to Premium"
        )

    try:
        user.credits_balance = current_balance - cost
        
        # Keep profile_settings synced for backwards compatibility
        settings = dict(user.profile_settings or {})
        settings["credits"] = user.credits_balance
        user.profile_settings = settings
        
        db.commit()
        db.refresh(user)
        log.info(
            "Credits deducted: user_id=%d, deducted=%d, new_balance=%d",
            user_id, cost, user.credits_balance
        )
        return True
    except Exception as exc:
        db.rollback()
        log.exception("Database error while deducting credits: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TRANSACTION_FAILED: Credit deduction could not be completed."
        ) from exc


def add_user_credits(db: Session, user_id: int, credits: int, reason: str = "") -> int:
    """
    Atomically add credits to a user's wallet.
    Returns the new credit balance.
    """
    if credits <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_AMOUNT: Credit addition must be a positive integer."
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="USER_NOT_FOUND: Candidate account does not exist."
        )

    try:
        current_balance = int(user.credits_balance if user.credits_balance is not None else 0)
        user.credits_balance = current_balance + credits
        
        # Sync profile_settings
        settings = dict(user.profile_settings or {})
        settings["credits"] = user.credits_balance
        user.profile_settings = settings
        
        db.commit()
        db.refresh(user)
        log.info(
            "Credits granted: user_id=%d, added=%d, new_balance=%d, reason='%s'",
            user_id, credits, user.credits_balance, reason
        )
        return user.credits_balance
    except Exception as exc:
        db.rollback()
        log.exception("Database error while adding credits: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CREDIT_GRANT_FAILED: Could not update wallet balance."
        ) from exc
