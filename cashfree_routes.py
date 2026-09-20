"""DigiPath Cashfree Payment Gateway & Neural Credit Wallet Router.

Integrates Cashfree Payment Gateway REST API v2023-08-01:
  - POST /api/payment/create-order  Initiate ₹50 / 300-credit Starter Pack order (5 req/min)
  - POST /api/payment/verify        Idempotent payment verification & wallet crediting
  - POST /api/payment/webhook       HMAC SHA-256 signature verified server-to-server webhook
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
import models
from utils.admin_auth import get_authenticated_user
from utils.error_audit import log_system_error

log = logging.getLogger("digipath.cashfree")

router = APIRouter(prefix="/payment", tags=["Cashfree Payment Gateway"])

# ── Cashfree Credentials & Environment Configuration ──────────────────────────
CASHFREE_APP_ID: str = os.getenv("CASHFREE_APP_ID", "TEST_CF_APP_ID_2026").strip()
CASHFREE_SECRET_KEY: str = os.getenv("CASHFREE_SECRET_KEY", "TEST_CF_SECRET_KEY_2026").strip()
CASHFREE_ENV: str = os.getenv("CASHFREE_ENV", "SANDBOX").strip().upper()
CASHFREE_API_VERSION: str = "2023-08-01"


def _cashfree_is_configured() -> bool:
    """Return True only when non-placeholder gateway credentials are configured."""
    return bool(
        CASHFREE_APP_ID
        and CASHFREE_SECRET_KEY
        and not CASHFREE_APP_ID.startswith("TEST_")
        and not CASHFREE_SECRET_KEY.startswith("TEST_")
    )

if CASHFREE_ENV == "PRODUCTION":
    CASHFREE_BASE_URL = "https://api.cashfree.com/pg"
else:
    CASHFREE_BASE_URL = "https://sandbox.cashfree.com/pg"

# Fixed Credit Pack Specification: ₹50 for 300 Credits
STARTER_PACK_AMOUNT: float = 50.00
STARTER_PACK_CREDITS: int = 300


def _get_limiter():
    """Resolve shared slowapi rate limiter singleton from app."""
    try:
        from app import limiter
        return limiter
    except Exception:
        return None


# ── Pydantic Request Schemas ──────────────────────────────────────────────────
class CreateOrderRequest(BaseModel):
    amount: float = Field(default=STARTER_PACK_AMOUNT, ge=1.0)
    credits_to_add: int = Field(default=STARTER_PACK_CREDITS, ge=1)
    return_url: Optional[str] = Field(default=None)


class VerifyOrderRequest(BaseModel):
    order_id: str = Field(..., min_length=3, max_length=120)


# ── 1. Create Cashfree Order ──────────────────────────────────────────────────
@router.post("/create-order", status_code=status.HTTP_200_OK)
async def create_order(
    request: Request,
    body: Optional[CreateOrderRequest] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Generate a Cashfree Payment Order and return payment_session_id for Drop Checkout v3.
    Rate limited to 5 requests per minute.
    """
    # Rate Limiting Guard (5 req/min)
    limiter = _get_limiter()
    if limiter:
        try:
            await limiter._check_request_limit(
                request,
                endpoint_func=create_order,
                rate_limit="5/minute",
                is_async=True,
            )
        except Exception:
            pass

    if not _cashfree_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PAYMENTS_NOT_CONFIGURED: Cashfree credentials have not been configured.",
        )

    # The product definition is server-side. Clients must never choose a price
    # or credit quantity for a payment order.
    order_amount = STARTER_PACK_AMOUNT
    credits_to_add = STARTER_PACK_CREDITS

    # Generate unique, traceable Order ID: DIGI_ORD_<TIMESTAMP>_<HEX>
    order_id = f"DIGI_ORD_{int(time.time())}_{uuid.uuid4().hex[:6].upper()}"

    # Base return URL for web redirect or modal completion
    host_url = str(request.base_url).rstrip("/")
    return_url = f"{host_url}/pricing?order_id={order_id}"
    notify_url = f"{host_url}/api/payment/webhook"

    cf_payload = {
        "order_id": order_id,
        "order_amount": float(order_amount),
        "order_currency": "INR",
        "customer_details": {
            "customer_id": f"CUST_{user.id}",
            "customer_email": user.email,
            "customer_phone": "9999999999",
            "customer_name": user.full_name or "Candidate",
        },
        "order_meta": {
            "return_url": return_url,
            "notify_url": notify_url,
            "payment_methods": "cc,dc,upi,nb",
        },
        "order_note": f"DigiPath Neural Credits Starter Pack ({credits_to_add} Credits)",
    }

    headers = {
        "x-client-id": CASHFREE_APP_ID,
        "x-client-secret": CASHFREE_SECRET_KEY,
        "x-api-version": CASHFREE_API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payment_session_id: Optional[str] = None

    # Call Cashfree API
    try:
        cf_response = requests.post(
            f"{CASHFREE_BASE_URL}/orders",
            json=cf_payload,
            headers=headers,
            timeout=10,
        )
        resp_data = cf_response.json() if cf_response.content else {}

        if cf_response.status_code in (200, 201) and "payment_session_id" in resp_data:
            payment_session_id = resp_data["payment_session_id"]
            log.info("Cashfree Order Created: order_id=%s, session_id=%s", order_id, payment_session_id)
        else:
            log.warning(
                "Cashfree Order API returned status %s: %s",
                cf_response.status_code, resp_data
            )
            # Log technical incident in SystemErrorLog
            log_system_error(
                db=db,
                module_name="CASHFREE_PAYMENT",
                error_message=f"Cashfree Create Order HTTP {cf_response.status_code}: {resp_data.get('message', resp_data)}",
                stack_trace=json.dumps(resp_data),
                user_id=user.id,
            )

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"CASHFREE_GATEWAY_ERROR: {resp_data.get('message', 'Failed to initialize payment session')}",
            )

    except requests.RequestException as net_err:
        log.exception("Cashfree network connection error: %s", net_err)
        log_system_error(
            db=db,
            module_name="CASHFREE_PAYMENT",
            error_message=f"Network exception connecting to Cashfree API: {net_err}",
            user_id=user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PAYMENT_GATEWAY_UNREACHABLE: Cashfree service is temporarily unreachable.",
        ) from net_err

    # Persist PENDING transaction in DB
    try:
        txn = models.Transaction(
            user_id=user.id,
            order_id=order_id,
            amount=float(order_amount),
            credits_added=int(credits_to_add),
            payment_status="PENDING",
            cf_payment_id=None,
            created_at=datetime.datetime.utcnow(),
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)
    except Exception as exc:
        db.rollback()
        log.exception("Failed to insert pending transaction into database: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DATABASE_TRANSACTION_ERROR: Could not record transaction initialization.",
        ) from exc

    return {
        "payment_session_id": payment_session_id,
        "order_id": order_id,
        "amount": float(order_amount),
        "credits": int(credits_to_add),
        "cf_env": CASHFREE_ENV,
    }


# ── 2. Verify Cashfree Payment & Idempotent Credit Grant ───────────────────────
@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify_payment(
    body: VerifyOrderRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_authenticated_user),
) -> Dict[str, Any]:
    """
    Verify order settlement status with Cashfree API and grant credits idempotently.
    Prevents double crediting if already verified.
    """
    order_id = body.order_id.strip()

    if not _cashfree_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PAYMENTS_NOT_CONFIGURED: Cashfree credentials have not been configured.",
        )

    # 1. Fetch transaction record from database
    txn = db.query(models.Transaction).filter(models.Transaction.order_id == order_id).first()
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TRANSACTION_NOT_FOUND: No transaction record found matching this Order ID.",
        )
    if txn.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TRANSACTION_ACCESS_DENIED: This order belongs to another account.",
        )

    # 2. Idempotency Check: if already marked PAID, return existing state immediately
    if txn.payment_status == "PAID":
        log.info("Idempotent verify check: Order %s already marked PAID.", order_id)
        return {
            "success": True,
            "order_id": order_id,
            "payment_status": "PAID",
            "already_processed": True,
            "new_balance": user.credits_balance,
            "message": "Order already settled and credited.",
        }

    # 3. Query Cashfree API to verify actual order status
    headers = {
        "x-client-id": CASHFREE_APP_ID,
        "x-client-secret": CASHFREE_SECRET_KEY,
        "x-api-version": CASHFREE_API_VERSION,
        "Accept": "application/json",
    }

    order_status = "UNKNOWN"
    cf_payment_id = None

    try:
        cf_res = requests.get(
            f"{CASHFREE_BASE_URL}/orders/{order_id}",
            headers=headers,
            timeout=10,
        )
        if cf_res.status_code == 200:
            order_data = cf_res.json()
            order_status = order_data.get("order_status", "UNKNOWN")
            
            # Fetch payments details if order is paid
            if order_status == "PAID":
                pay_res = requests.get(
                    f"{CASHFREE_BASE_URL}/orders/{order_id}/payments",
                    headers=headers,
                    timeout=10,
                )
                if pay_res.status_code == 200 and isinstance(pay_res.json(), list) and len(pay_res.json()) > 0:
                    cf_payment_id = str(pay_res.json()[0].get("cf_payment_id") or "")
        else:
            log.warning("Cashfree Order check returned HTTP %d: %s", cf_res.status_code, cf_res.text)
    except Exception as fetch_err:
        log.warning("Cashfree order query exception: %s", fetch_err)

    # 4. Handle Verification Result
    if order_status == "PAID":
        try:
            txn.payment_status = "PAID"
            txn.cf_payment_id = cf_payment_id or f"CF_{int(time.time())}"
            
            # Atomically credit wallet
            current_bal = int(user.credits_balance if user.credits_balance is not None else 0)
            user.credits_balance = current_bal + txn.credits_added

            # Sync profile_settings
            settings = dict(user.profile_settings or {})
            settings["credits"] = user.credits_balance
            user.profile_settings = settings

            db.commit()
            db.refresh(user)
            db.refresh(txn)

            log.info(
                "Payment successfully verified: Order=%s, User=%s, Credits Added=+%d, New Balance=%d",
                order_id, user.email, txn.credits_added, user.credits_balance
            )
            return {
                "success": True,
                "order_id": order_id,
                "payment_status": "PAID",
                "credits_added": txn.credits_added,
                "new_balance": user.credits_balance,
                "message": f"Payment verified successfully! Added +{txn.credits_added} credits to your wallet.",
            }
        except Exception as exc:
            db.rollback()
            log.exception("Error applying wallet balance on order %s: %s", order_id, exc)
            log_system_error(
                db=db,
                module_name="CASHFREE_PAYMENT",
                error_message=f"Database commit error during wallet credit for order {order_id}: {exc}",
                user_id=user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="WALLET_CREDIT_FAILED: Could not finalize balance update. Admin team notified.",
            ) from exc

    else:
        # Payment failed or not paid
        txn.payment_status = "FAILED"
        log_system_error(
            db=db,
            module_name="CASHFREE_PAYMENT",
            error_message=f"Order {order_id} verification failed: Cashfree status is '{order_status}'",
            user_id=user.id,
        )
        db.commit()
        return {
            "success": False,
            "order_id": order_id,
            "payment_status": order_status,
            "new_balance": user.credits_balance,
            "message": f"Payment verification failed with status '{order_status}'.",
        }


# ── 3. Cashfree Server-to-Server Webhook ───────────────────────────────────────
@router.post("/webhook", status_code=status.HTTP_200_OK)
async def cashfree_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    """
    Handle asynchronous Cashfree payment notification webhooks.
    Validates HMAC SHA-256 signature using CASHFREE_SECRET_KEY before processing.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-webhook-signature") or request.headers.get("x-cashfree-signature") or ""
    timestamp = request.headers.get("x-webhook-timestamp") or ""

    if not _cashfree_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="PAYMENTS_NOT_CONFIGURED")
    if not signature or not timestamp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MISSING_WEBHOOK_SIGNATURE")
    try:
            data_to_sign = timestamp.encode("utf-8") + raw_body
            computed_hmac = hmac.new(
                CASHFREE_SECRET_KEY.encode("utf-8"),
                data_to_sign,
                hashlib.sha256,
            ).digest()
            computed_signature = base64.b64encode(computed_hmac).decode("utf-8")

            if not hmac.compare_digest(signature, computed_signature):
                log.warning("Invalid Cashfree webhook signature received.")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="INVALID_WEBHOOK_SIGNATURE: Cryptographic signature mismatch."
                )
    except HTTPException:
        raise
    except Exception as sig_err:
        log.warning("Signature validation exception: %s", sig_err)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_WEBHOOK_SIGNATURE") from sig_err

    try:
        payload = json.loads(raw_body.decode("utf-8", errors="ignore"))
    except Exception as parse_err:
        log.warning("Webhook payload JSON parse failed: %s", parse_err)
        return {"status": "INVALID_PAYLOAD"}

    event_type = payload.get("type", "")
    data_obj = payload.get("data", {})
    order_obj = data_obj.get("order", {})
    order_id = order_obj.get("order_id") or data_obj.get("order_id")

    payment_obj = data_obj.get("payment", {})
    payment_status = payment_obj.get("payment_status") or order_obj.get("order_status") or ""
    cf_payment_id = str(payment_obj.get("cf_payment_id") or "")

    log.info("Cashfree Webhook Event: type=%s, order_id=%s, status=%s", event_type, order_id, payment_status)

    if order_id and payment_status in ("SUCCESS", "PAID"):
        txn = db.query(models.Transaction).filter(models.Transaction.order_id == order_id).first()
        if txn and txn.payment_status != "PAID":
            txn_user = db.query(models.User).filter(models.User.id == txn.user_id).first()
            if txn_user:
                txn.payment_status = "PAID"
                txn.cf_payment_id = cf_payment_id or txn.cf_payment_id or f"CF_HOOK_{int(time.time())}"
                
                # Atomically credit wallet
                current_bal = int(txn_user.credits_balance if txn_user.credits_balance is not None else 0)
                txn_user.credits_balance = current_bal + txn.credits_added

                settings = dict(txn_user.profile_settings or {})
                settings["credits"] = txn_user.credits_balance
                txn_user.profile_settings = settings

                db.commit()
                log.info("Webhook applied wallet credit: User=%s, Added=%d", txn_user.email, txn.credits_added)

    return {"status": "OK"}
