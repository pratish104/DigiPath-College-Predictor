"""DigiPath Credit, Permissions, and User Management Ledger.

Manages user credit balances, tier upgrades (Free vs. Premium), account moderation
(Active vs. Banned), automatic reward disbursements (+5 credits per verified scam report),
and platform administrator permissions.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
import models

log = logging.getLogger("digipath.user_service")

DEFAULT_FREE_DOWNLOADS = 3
REWARD_PER_VERIFIED_SCAM = 5


class UserService:
    """Encapsulates credit ledger, permission toggles, and user moderation matrix."""

    @staticmethod
    def get_user_credit_state(user: models.User) -> Dict[str, Any]:
        """Extract or initialize credit and tier state from user profile settings."""
        settings = dict(user.profile_settings or {})
        is_admin_role = (user.role or "").upper() == "ADMIN"
        is_premium = bool(settings.get("is_premium", False) or is_admin_role)
        is_banned = bool(settings.get("is_banned", False))

        # Ensure default keys exist
        if "free_pdf_downloads" not in settings:
            settings["free_pdf_downloads"] = 9999 if (is_admin_role or is_premium) else DEFAULT_FREE_DOWNLOADS
        if "credits" not in settings:
            settings["credits"] = 9999 if (is_admin_role or is_premium) else 0
        if "notifications" not in settings or not isinstance(settings["notifications"], list):
            settings["notifications"] = []

        free_downloads = int(settings.get("free_pdf_downloads", DEFAULT_FREE_DOWNLOADS))
        credits = int(settings.get("credits", 0))
        total_available = free_downloads + credits

        return {
            "user_id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_admin": is_admin_role,
            "is_premium": is_premium,
            "tier": "PREMIUM" if is_premium else "FREE",
            "is_banned": is_banned,
            "status": "BANNED" if is_banned else "ACTIVE",
            "free_pdf_downloads": free_downloads,
            "credits": credits,
            "total_available": total_available,
            "can_download": (is_admin_role or is_premium or total_available > 0) and not is_banned,
            "notifications": settings.get("notifications", [])[-10:],
        }

    @staticmethod
    def can_download_pdf(user: models.User) -> Tuple[bool, str]:
        """Check if user has remaining free downloads, active premium tier, or credits."""
        state = UserService.get_user_credit_state(user)
        if state["is_banned"]:
            return False, "ACCOUNT_SUSPENDED: Your access has been deactivated by an administrator."
        if state["is_admin"]:
            return True, "Administrator privilege: unlimited downloads authorized."
        if state["is_premium"]:
            return True, "Premium subscription: unlimited downloads authorized."
        if state["free_pdf_downloads"] > 0:
            return True, f"Free download available ({state['free_pdf_downloads']} remaining)"
        if state["credits"] > 0:
            return True, f"Credit balance available ({state['credits']} credits remaining)"
        return False, "INSUFFICIENT_CREDITS: You have 0 free downloads and 0 credits. Submit a verified scam report to earn +5 credits."

    @staticmethod
    def deduct_download_credit(user_id: int, db: Session) -> Dict[str, Any]:
        """Deduct 1 download credit from free quota first, then from credit balance."""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        state = UserService.get_user_credit_state(user)
        if state["is_banned"]:
            return {
                "success": False,
                "message": "ACCOUNT_SUSPENDED: Access deactivated by an administrator.",
                "free_pdf_downloads": 0,
                "credits": 0,
                "total_available": 0,
            }

        if state["is_admin"] or state["is_premium"]:
            return {
                "success": True,
                "deducted_from": "unlimited_tier",
                "free_pdf_downloads": 9999,
                "credits": 9999,
                "total_available": 9999,
                "message": "Unlimited download authorized for current tier.",
            }

        settings = dict(user.profile_settings or {})
        free_downloads = int(settings.get("free_pdf_downloads", DEFAULT_FREE_DOWNLOADS))
        credits = int(settings.get("credits", 0))

        if free_downloads > 0:
            settings["free_pdf_downloads"] = free_downloads - 1
            deducted_from = "free_quota"
        elif credits > 0:
            settings["credits"] = credits - 1
            deducted_from = "credits"
        else:
            return {
                "success": False,
                "message": "INSUFFICIENT_CREDITS: Submit a verified scam report to earn +5 credits.",
                "free_pdf_downloads": 0,
                "credits": 0,
                "total_available": 0,
            }

        user.profile_settings = settings
        try:
            db.add(user)
            db.commit()
            db.refresh(user)
        except Exception as exc:
            db.rollback()
            log.error("Failed to commit credit deduction for user %d: %s", user_id, exc)
            raise

        total_available = settings["free_pdf_downloads"] + settings["credits"]
        log.info("Deducted 1 download allowance (%s) for user %s. Remaining: %d", deducted_from, user.email, total_available)
        
        return {
            "success": True,
            "deducted_from": deducted_from,
            "free_pdf_downloads": settings["free_pdf_downloads"],
            "credits": settings["credits"],
            "total_available": total_available,
            "message": "Download credit successfully applied.",
        }

    @staticmethod
    def deduct_credits(user_id: int, db: Session) -> Dict[str, Any]:
        """Alias for deduct_download_credit."""
        return UserService.deduct_download_credit(user_id, db)

    @staticmethod
    def award_credits(
        user_id: int,
        amount: int,
        reason: str,
        db: Session
    ) -> Dict[str, Any]:
        """Disburse credits to a user with an attached notification log."""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        settings = dict(user.profile_settings or {})
        current_credits = int(settings.get("credits", 0))
        new_credits = current_credits + max(0, amount)
        settings["credits"] = new_credits

        notifications = list(settings.get("notifications", []))
        notifications.append({
            "id": f"notif-{int(datetime.datetime.utcnow().timestamp() * 1000)}",
            "type": "CREDIT_REWARD",
            "amount": amount,
            "message": reason,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "read": False,
        })
        settings["notifications"] = notifications[-20:]

        user.profile_settings = settings
        try:
            db.add(user)
            db.commit()
            db.refresh(user)
        except Exception as exc:
            db.rollback()
            log.error("Failed to disburse credits to user %d: %s", user_id, exc)
            raise

        log.info("Awarded +%d credits to %s (New Balance: %d). Reason: %s", amount, user.email, new_credits, reason)
        return {
            "success": True,
            "user_id": user.id,
            "awarded_amount": amount,
            "new_credit_balance": new_credits,
            "reason": reason,
        }

    @staticmethod
    def add_credits(user_id: int, amount: int, db: Session, reason: str = "Admin Credit Reward") -> Dict[str, Any]:
        """Alias for award_credits."""
        return UserService.award_credits(user_id=user_id, amount=amount, reason=reason, db=db)

    @staticmethod
    def adjust_user_credits(user_id: int, credits_to_add: int, db: Session) -> Dict[str, Any]:
        """Manually add or modify credit balance for a user via Admin Console."""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        settings = dict(user.profile_settings or {})
        current_credits = int(settings.get("credits", 0))
        new_credits = max(0, current_credits + credits_to_add)
        settings["credits"] = new_credits

        notifications = list(settings.get("notifications", []))
        notifications.append({
            "id": f"notif-{int(datetime.datetime.utcnow().timestamp() * 1000)}",
            "type": "ADMIN_ADJUSTMENT",
            "amount": credits_to_add,
            "message": f"Administrator adjusted your credit balance by {credits_to_add:+d} credits.",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "read": False,
        })
        settings["notifications"] = notifications[-20:]

        user.profile_settings = settings
        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "success": True,
            "user_id": user.id,
            "new_credit_balance": new_credits,
            "adjustment": credits_to_add,
        }

    @staticmethod
    def toggle_user_premium(user_id: int, db: Session) -> Dict[str, Any]:
        """Toggle user between Free and Premium tiers."""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        settings = dict(user.profile_settings or {})
        current_state = bool(settings.get("is_premium", False))
        new_state = not current_state
        settings["is_premium"] = new_state

        if new_state:
            settings["free_pdf_downloads"] = 9999
            settings["credits"] = 9999
        else:
            settings["free_pdf_downloads"] = DEFAULT_FREE_DOWNLOADS
            settings["credits"] = 0

        user.profile_settings = settings
        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "success": True,
            "user_id": user.id,
            "is_premium": new_state,
            "tier": "PREMIUM" if new_state else "FREE",
        }

    @staticmethod
    def toggle_user_ban(user_id: int, db: Session) -> Dict[str, Any]:
        """Toggle account active status (Active vs. Banned)."""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        settings = dict(user.profile_settings or {})
        current_banned = bool(settings.get("is_banned", False))
        new_banned = not current_banned
        settings["is_banned"] = new_banned

        user.profile_settings = settings
        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "success": True,
            "user_id": user.id,
            "is_banned": new_banned,
            "status": "BANNED" if new_banned else "ACTIVE",
        }

    @staticmethod
    def get_all_users_matrix(db: Session) -> List[Dict[str, Any]]:
        """Retrieve all users formatted for the Admin Permissions Matrix."""
        users = db.query(models.User).order_by(models.User.id.asc()).all()
        matrix = []
        for u in users:
            settings = dict(u.profile_settings or {})
            is_admin = (u.role or "").upper() == "ADMIN"
            is_premium = bool(settings.get("is_premium", False) or is_admin)
            is_banned = bool(settings.get("is_banned", False))
            credits = int(settings.get("credits", 9999 if is_admin else 0))

            matrix.append({
                "user_id": u.id,
                "username": u.full_name or u.email.split("@")[0],
                "email": u.email,
                "role": "ADMIN" if is_admin else "USER",
                "is_admin": is_admin,
                "is_premium": is_premium,
                "tier": "PREMIUM" if is_premium else "FREE",
                "is_banned": is_banned,
                "status": "BANNED" if is_banned else "ACTIVE",
                "credit_balance": credits,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            })
        return matrix

    @staticmethod
    def seed_admin_user(db: Session) -> Optional[models.User]:
        """Optionally provision the first administrator from explicit environment settings.

        A public deployment must never create an account with a known password.
        Set ``BOOTSTRAP_ADMIN_EMAIL`` and ``BOOTSTRAP_ADMIN_PASSWORD`` only for
        the first deployment, then remove the password variable afterwards.
        """
        import auth_service
        import os

        admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not admin_email or len(admin_password) < 12:
            log.warning(
                "Administrator bootstrap skipped. Configure BOOTSTRAP_ADMIN_EMAIL "
                "and a 12+ character BOOTSTRAP_ADMIN_PASSWORD to provision the first admin."
            )
            return None
        try:
            admin_user = db.query(models.User).filter(models.User.email == admin_email).first()
            if not admin_user:
                admin_user = models.User(
                    full_name="System Administrator",
                    email=admin_email,
                    hashed_password=auth_service.get_password_hash(admin_password),
                    role="ADMIN",
                    admin_level=2,
                    credits_balance=9999,
                    career_interests=["Cybersecurity", "Cloud Architecture", "DevOps"],
                    profile_settings={
                        "free_pdf_downloads": 9999,
                        "credits": 9999,
                        "is_admin": True,
                        "is_premium": True,
                        "is_banned": False,
                        "notifications": [
                            {
                                "id": "notif-init",
                                "type": "SYSTEM",
                                "amount": 0,
                                "message": "Root Administrator privileges initialized with Level-5 clearance.",
                                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                                "read": True,
                            }
                        ],
                    }
                )
                db.add(admin_user)
                db.commit()
                db.refresh(admin_user)
                log.info("Administrator bootstrap account provisioned: %s", admin_email)
            else:
                updated = False
                if admin_user.role not in ("ADMIN", "SUPER_ADMIN"):
                    admin_user.role = "SUPER_ADMIN"
                    updated = True
                if (admin_user.admin_level or 0) < 2:
                    admin_user.admin_level = 2
                    updated = True
                if (admin_user.credits_balance or 0) < 1000:
                    admin_user.credits_balance = 9999
                    updated = True
                if updated:
                    db.add(admin_user)
                    db.commit()
            return admin_user
        except Exception as exc:
            db.rollback()
            log.warning("Admin account seeding notice: %s", exc)
            return None
