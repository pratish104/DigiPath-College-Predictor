"""DigiPath Multi-Level Admin Control Panel & Audit Controller.

Endpoints protected by RBAC require_admin_level(1) or require_admin_level(2):
  - GET  /admin/dashboard              Render web3 admin console with real-time metrics
  - GET  /api/admin/premium-users      Directory of users, credit balances, and lifetime spend
  - GET  /api/admin/error-logs         Audit log of system/ML/gateway exceptions
  - POST /api/admin/grant-credits      RBAC-guarded manual credit disbursements
  - POST /api/admin/resolve-error/{id} Mark technical incident resolved
  - GET  /api/admin/transactions       Full ledger of Cashfree payment orders
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from database import get_db
import models
from utils.admin_auth import require_admin_level

log = logging.getLogger("digipath.admin")

router = APIRouter(tags=["Admin Control Panel"])


# ── Pydantic Request Schemas ──────────────────────────────────────────────────
class GrantCreditsPayload(BaseModel):
    user_id: int
    credits_to_add: int = Field(..., ge=1, le=10000)
    reason: Optional[str] = Field(default="Administrative grant")


# ── Helper for Jinja2 templates ───────────────────────────────────────────────
def _get_templates():
    from app import templates
    return templates


# ── 1. Admin Dashboard Page Controller ─────────────────────────────────────────
@router.get("/admin/dashboard", response_class=HTMLResponse)
@router.get("/admin-dashboard", response_class=HTMLResponse)
@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_view(
    request: Request,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin_level(1)),
):
    """
    Render the Web3 Admin Console with executive KPI metrics:
      - Total Platform Revenue (Sum of PAID Cashfree transactions)
      - Total Registered Users
      - Count of Premium Users (Wallet balance > 50 credits)
      - Count of Unresolved Technical Error Incidents
    """
    templates = _get_templates()

    # Calculate real-time metrics
    total_revenue_result = (
        db.query(func.sum(models.Transaction.amount))
        .filter(models.Transaction.payment_status == "PAID")
        .scalar()
    )
    total_revenue = float(total_revenue_result or 0.0)

    total_users = db.query(func.count(models.User.id)).scalar() or 0

    premium_users_count = (
        db.query(func.count(models.User.id))
        .filter(models.User.credits_balance > 50)
        .scalar()
        or 0
    )

    unresolved_errors_count = (
        db.query(func.count(models.SystemErrorLog.id))
        .filter(models.SystemErrorLog.status == "UNRESOLVED")
        .scalar()
        or 0
    )

    total_transactions_count = db.query(func.count(models.Transaction.id)).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "user": admin,
            "admin_level": admin.admin_level or (2 if (admin.role or "").upper() in ("ADMIN", "SUPER_ADMIN") else 1),
            "total_revenue": total_revenue,
            "total_users": total_users,
            "premium_users_count": premium_users_count,
            "unresolved_errors_count": unresolved_errors_count,
            "total_transactions_count": total_transactions_count,
        },
    )


# ── 2. Premium Users Directory ────────────────────────────────────────────────
@router.get("/api/admin/premium-users")
async def list_premium_users(
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin_level(1)),
    limit: int = Query(default=50, ge=1, le=200),
) -> List[Dict[str, Any]]:
    """
    List candidate users sorted by credit balance and lifetime financial contribution.
    Includes transaction history array for each user.
    """
    users = (
        db.query(models.User)
        .order_by(desc(models.User.credits_balance), desc(models.User.created_at))
        .limit(limit)
        .all()
    )

    result = []
    for u in users:
        # Calculate lifetime spent on platform
        spent_sum = (
            db.query(func.sum(models.Transaction.amount))
            .filter(
                models.Transaction.user_id == u.id,
                models.Transaction.payment_status == "PAID"
            )
            .scalar()
        )
        lifetime_spent = float(spent_sum or 0.0)

        # Retrieve recent transactions
        recent_txns = (
            db.query(models.Transaction)
            .filter(models.Transaction.user_id == u.id)
            .order_by(desc(models.Transaction.created_at))
            .limit(5)
            .all()
        )

        txn_list = [
            {
                "order_id": t.order_id,
                "amount": t.amount,
                "credits_added": t.credits_added,
                "payment_status": t.payment_status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in recent_txns
        ]

        result.append({
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "role": u.role or "USER",
            "admin_level": u.admin_level or 0,
            "credits_balance": int(u.credits_balance if u.credits_balance is not None else 50),
            "lifetime_spent": lifetime_spent,
            "is_premium": (u.credits_balance or 0) > 50,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "recent_transactions": txn_list,
        })

    return result


# ── 3. Technical Error Logs Audit ─────────────────────────────────────────────
@router.get("/api/admin/error-logs")
async def list_error_logs(
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin_level(1)),
    status_filter: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> List[Dict[str, Any]]:
    """
    Retrieve the latest 50 system errors, optionally filtered by status ('UNRESOLVED' | 'RESOLVED').
    """
    query = db.query(models.SystemErrorLog)
    
    if status_filter and status_filter.upper() in ("UNRESOLVED", "RESOLVED"):
        query = query.filter(models.SystemErrorLog.status == status_filter.upper())

    logs = query.order_by(desc(models.SystemErrorLog.timestamp)).limit(limit).all()

    return [
        {
            "id": err.id,
            "user_id": err.user_id,
            "module_name": err.module_name,
            "error_message": err.error_message,
            "stack_trace": err.stack_trace,
            "status": err.status,
            "timestamp": err.timestamp.isoformat() if err.timestamp else None,
        }
        for err in logs
    ]


# ── 4. RBAC-Guarded Credit Granting ────────────────────────────────────────────
@router.post("/api/admin/grant-credits")
async def grant_credits(
    payload: GrantCreditsPayload,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin_level(1)),
) -> Dict[str, Any]:
    """
    Manually disburse neural credits to a user wallet with strict RBAC limits:
      - Support Admins (Admin Level 1): Maximum 300 credits per transaction.
      - Super Admins (Admin Level 2): Unlimited grants.
    """
    admin_lvl = int(admin.admin_level or 0)
    if (admin.role or "").upper() in ("SUPER_ADMIN", "ADMIN"):
        admin_lvl = max(admin_lvl, 2)

    # Enforce RBAC limit for Support Admins
    if admin_lvl < 2 and payload.credits_to_add > 300:
        log.warning(
            "RBAC limit exceeded by admin %s (Level %d): attempted to grant %d credits",
            admin.email, admin_lvl, payload.credits_to_add
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "RBAC_LIMIT_EXCEEDED: Support Admins (Level 1) can disburse a maximum of "
                "300 credits per grant. Contact a Super Admin (Level 2) for higher allocations."
            ),
        )

    target_user = db.query(models.User).filter(models.User.id == payload.user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="USER_NOT_FOUND: The target user account does not exist.",
        )

    try:
        current_balance = int(target_user.credits_balance if target_user.credits_balance is not None else 0)
        target_user.credits_balance = current_balance + payload.credits_to_add

        # Sync profile_settings credits key
        settings = dict(target_user.profile_settings or {})
        settings["credits"] = target_user.credits_balance
        target_user.profile_settings = settings

        # Also insert an internal transaction audit record for bookkeeping
        audit_txn = models.Transaction(
            user_id=target_user.id,
            order_id=f"ADMIN_GRANT_{admin.id}_{int(datetime.datetime.utcnow().timestamp())}",
            amount=0.0,
            credits_added=payload.credits_to_add,
            payment_status="PAID",
            cf_payment_id=f"ADMIN_{admin.email[:15]}",
            created_at=datetime.datetime.utcnow(),
        )
        db.add(audit_txn)
        db.commit()
        db.refresh(target_user)

        log.info(
            "Admin %s (Level %d) granted %d credits to user %s. New balance: %d. Reason: %s",
            admin.email, admin_lvl, payload.credits_to_add, target_user.email,
            target_user.credits_balance, payload.reason
        )

        return {
            "success": True,
            "user_id": target_user.id,
            "user_email": target_user.email,
            "credits_added": payload.credits_to_add,
            "new_balance": target_user.credits_balance,
            "message": f"Successfully granted {payload.credits_to_add} credits to {target_user.email}.",
        }
    except Exception as exc:
        db.rollback()
        log.exception("Error executing manual credit grant: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CREDIT_GRANT_FAILED: Could not finalize balance update in database.",
        ) from exc


# ── 5. Resolve Error Incident ─────────────────────────────────────────────────
@router.post("/api/admin/resolve-error/{error_id}")
async def resolve_error(
    error_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin_level(1)),
) -> Dict[str, Any]:
    """Mark a system error incident as RESOLVED."""
    incident = db.query(models.SystemErrorLog).filter(models.SystemErrorLog.id == error_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="INCIDENT_NOT_FOUND: Error log entry does not exist.",
        )

    incident.status = "RESOLVED"
    db.commit()
    db.refresh(incident)

    log.info("Error log #%d marked RESOLVED by admin %s", error_id, admin.email)
    return {
        "success": True,
        "error_id": incident.id,
        "status": incident.status,
        "message": f"Incident #{incident.id} marked as RESOLVED.",
    }


# ── 6. Transactions Audit Ledger ──────────────────────────────────────────────
@router.get("/api/admin/transactions")
async def list_transactions(
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin_level(1)),
    status_filter: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=300),
) -> List[Dict[str, Any]]:
    """Retrieve full audit log of payment orders and manual credit grants."""
    query = db.query(models.Transaction)
    if status_filter and status_filter.upper() in ("PAID", "PENDING", "FAILED"):
        query = query.filter(models.Transaction.payment_status == status_filter.upper())

    txns = query.order_by(desc(models.Transaction.created_at)).limit(limit).all()

    result = []
    for t in txns:
        user_email = "Unknown"
        user_name = "Candidate"
        if t.user:
            user_email = t.user.email
            user_name = t.user.full_name

        result.append({
            "id": t.id,
            "user_id": t.user_id,
            "user_email": user_email,
            "user_name": user_name,
            "order_id": t.order_id,
            "amount": t.amount,
            "credits_added": t.credits_added,
            "payment_status": t.payment_status,
            "cf_payment_id": t.cf_payment_id or "—",
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    return result
