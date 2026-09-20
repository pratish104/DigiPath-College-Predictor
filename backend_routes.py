"""DigiPath Backend API Routes.

Comprehensive router providing:
- Institute search, filters, and detail analytics
- Job recommendations and skill matching
- Dual-pathway CET / Diploma admissions predictor
- Resilient PDF, CSV, Excel, and JSON prediction report generation
- RLHF chatbot query & reinforcement feedback
- Multi-channel scam & fraud detection (Text, Document, URL)
- Global threat & company blacklisting registry
- User tier (Free vs Premium) & permissions matrix
- User credit ledger & PDF download quota management
- Admin moderation panel with +5 credit rewards
- Matplotlib visual analytics chart streaming
- Career interests and authenticated user profile management
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import models
from analytics_service import analytics_service
from auth_routes import COOKIE_NAME, get_current_admin_user
from auth_service import get_token_payload
from cet_predictor import CETPredictor, DiplomaPredictor, PredictRequest, student_category_options
from chatbot_service import RLHFAssistant
from data_loader import DataLoader
from database import get_db
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from job_service import JobService
from pydantic import BaseModel, Field
from report_generator import generate_report
from scam_service import scam_service
from sqlalchemy.orm import Session
from trend_analyzer import TrendAnalyzer
from user_service import UserService

log = logging.getLogger("digipath.backend_routes")

router = APIRouter()

# Singletons initialized
data_loader = DataLoader()
cet_engine = CETPredictor(data_loader)
diploma_engine = DiplomaPredictor(data_loader)
trends_engine = TrendAnalyzer(data_loader)
job_service = JobService.get_instance("data")
rlhf_assistant = RLHFAssistant(data_loader)


# ── Pydantic Request Models ──────────────────────────────────────────────────

class FeedbackCreate(BaseModel):
    msg: str = Field(min_length=1, max_length=2000)
    rating: int = Field(ge=1, le=5)
    target: Optional[str] = Field(default=None, max_length=255)


class JobRecommendRequest(BaseModel):
    skills: List[str] = Field(default_factory=list)
    role: Optional[str] = None
    limit: int = Field(default=18, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ScamCheckRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=50000)
    company_name: Optional[str] = None
    gstin: Optional[str] = None


class ScamUrlRequest(BaseModel):
    url: str = Field(min_length=3, max_length=2048)


class ScamReportCreate(BaseModel):
    scammer_entity: str = Field(min_length=1, max_length=255)
    platform: Optional[str] = Field(default="Web", max_length=100)
    scam_type: str = Field(default="Job Scam", max_length=100)
    description: str = Field(min_length=1)
    evidence_url: Optional[str] = Field(default=None, max_length=2048)


class AddBlacklistRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    domain: Optional[str] = Field(default=None, max_length=255)
    scam_pattern: Optional[str] = Field(default=None)
    severity: Optional[str] = Field(default="CRITICAL", max_length=50)


class CreditAdjustmentRequest(BaseModel):
    credits_to_add: int = Field(default=10)


class ChatQueryRequest(BaseModel):
    query: str = Field(min_length=1)


class ChatFeedbackRequest(BaseModel):
    message_id: str = Field(min_length=1)
    score: int = Field(ge=-1, le=1)


class CETRequestLegacy(BaseModel):
    percentile: float = Field(..., ge=0.0, le=100.0)
    category: str
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    eligible_for_ladies: bool = False
    special_eligibilities: List[str] = Field(default_factory=list)
    university_scope: Optional[str] = "ANY"
    extra_filters: Optional[dict] = None


class DiplomaRequestLegacy(BaseModel):
    percentage: float = Field(..., ge=0.0, le=100.0)
    category: str
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    eligible_for_ladies: bool = False
    special_eligibilities: List[str] = Field(default_factory=list)
    university_scope: Optional[str] = "ANY"
    extra_filters: Optional[dict] = None


class InterestUpdate(BaseModel):
    interests: List[str]


class ReportExportPayload(BaseModel):
    percentile: Optional[float] = None
    percentage: Optional[float] = None
    score: Optional[float] = None
    category: Optional[str] = "OPEN"
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    eligible_for_ladies: bool = False
    special_eligibilities: List[str] = Field(default_factory=list)
    university_scope: Optional[str] = "ANY"
    pathway: Optional[str] = "fe"
    extra_filters: Optional[dict] = None
    results: Optional[List[Dict[str, Any]]] = None


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=120)
    roll_number: Optional[str] = Field(default=None, max_length=64)
    preferred_branch: Optional[str] = Field(default=None, max_length=120)
    category: Optional[str] = Field(default=None, max_length=64)
    target_city: Optional[str] = Field(default=None, max_length=120)


_AVATAR_DIR = Path(__file__).resolve().parent / "static" / "uploads" / "avatars"
_MAX_AVATAR_BYTES = 2 * 1024 * 1024


def _avatar_extension(content: bytes) -> Optional[str]:
    """Recognize a small allowlist by file signature, not client MIME/name."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    return None


def _remove_avatar(avatar_url: Optional[str]) -> None:
    """Remove only a previously generated local avatar path."""
    if not avatar_url or not avatar_url.startswith("/static/uploads/avatars/"):
        return
    candidate = _AVATAR_DIR / Path(avatar_url).name
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        log.warning("Unable to remove old profile avatar.")


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class BookmarkCollegeRequest(BaseModel):
    dte_code: str = Field(min_length=1, max_length=32)
    name: Optional[str] = None
    city: Optional[str] = None


class BookmarkJobRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)
    title: Optional[str] = None
    company: Optional[str] = None


# ── Auth & User Resolution Helper ────────────────────────────────────────────

def _extract_optional_user(
    request: Request,
    db: Session,
    authorization: Optional[str] = None,
) -> Optional[models.User]:
    """Extract authenticated database user from Bearer header or cookie with graceful fallback."""
    token: Optional[str] = None
    
    if authorization:
        scheme, _, bearer_token = authorization.partition(" ")
        if scheme.lower() == "bearer" and bearer_token.strip():
            token = bearer_token.strip()
        elif authorization.strip() and not bearer_token:
            token = authorization.strip()
            
    if not token and request is not None:
        cookie_val = request.cookies.get(COOKIE_NAME, "").strip()
        if cookie_val:
            scheme, _, cookie_token = cookie_val.partition(" ")
            if scheme.lower() == "bearer" and cookie_token.strip():
                token = cookie_token.strip()
            elif cookie_val and not cookie_token:
                token = cookie_val

    if not token:
        return None

    payload = get_token_payload(token)
    if not payload or not payload.get("sub"):
        return None

    email = str(payload.get("sub")).strip().lower()
    try:
        return db.query(models.User).filter(models.User.email == email).first()
    except Exception as exc:
        log.warning("Database user lookup error in auth helper: %s", exc)
        return None


# ── Credit & Reward Endpoints ────────────────────────────────────────────────

@router.get("/user/credits")
async def get_user_credits_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Return user's remaining free PDF downloads, tier, and credit balance."""
    user = _extract_optional_user(request, db, authorization)
    if not user:
        return {
            "free_pdf_downloads": 3,
            "credits": 0,
            "total_available": 3,
            "tier": "FREE",
            "is_premium": False,
            "can_download": True,
            "is_guest": True,
        }
    state = UserService.get_user_credit_state(user)
    state["is_guest"] = False
    return state


@router.post("/user/credits/deduct")
async def deduct_credit_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Deduct 1 download allowance when generating an ATS resume or report PDF."""
    user = _extract_optional_user(request, db, authorization)
    if not user:
        return {
            "success": True,
            "deducted_from": "guest_session",
            "free_pdf_downloads": 3,
            "credits": 0,
            "total_available": 3,
            "message": "Guest download authorized.",
        }
    
    result = UserService.deduct_download_credit(user.id, db)
    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=result["message"]
        )
    return result


# ── Admin Moderation & Blacklist Endpoints ───────────────────────────────────

@router.get("/admin/reports")
async def list_admin_scam_reports(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Retrieve list of candidate-submitted fraud reports for admin moderation."""
    user = _extract_optional_user(request, db, authorization)
    if not user or (user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    reports = db.query(models.ScamReport).order_by(models.ScamReport.created_at.desc()).all()
    total_users = db.query(models.User).count()
    verified_count = sum(1 for r in reports if r.status == "VERIFIED_SCAM")
    pending_count = sum(1 for r in reports if (r.status or "PENDING") == "PENDING")
    
    report_list = []
    for r in reports:
        reporter = db.query(models.User).filter(models.User.id == r.user_id).first() if r.user_id else None
        report_list.append({
            "id": r.id,
            "user_id": r.user_id,
            "user_email": reporter.email if reporter else "Anonymous Candidate",
            "scammer_entity": r.scammer_entity,
            "platform": r.platform,
            "scam_type": r.scam_type,
            "description": r.description,
            "evidence_url": r.evidence_url,
            "status": r.status or "PENDING",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "reports": report_list,
        "stats": {
            "total_users": total_users,
            "pending_reports": pending_count,
            "verified_scams": verified_count,
            "total_credits_awarded": verified_count * 5,
        }
    }


@router.post("/admin/reports/{report_id}/verify")
async def verify_scam_report(
    report_id: int,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Verify reported scam entity: marks VERIFIED_SCAM, adds to Global Blacklist, and rewards +5 credits."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")

    report = db.query(models.ScamReport).filter(models.ScamReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Scam report not found")

    report.status = "VERIFIED_SCAM"
    db.add(report)
    db.commit()

    # Automatically add to Global Blacklist Registry
    scam_service.add_to_blacklist(
        company_name=report.scammer_entity,
        domain=report.evidence_url,
        scam_pattern=report.description or "Confirmed recruitment fraud reported and verified by Root Moderation.",
        severity="CRITICAL"
    )

    credits_awarded = 0
    if report.user_id:
        try:
            reward_res = UserService.award_credits(
                user_id=report.user_id,
                amount=5,
                reason=f"Verified fraud intelligence report for '{report.scammer_entity}'. +5 PDF Credits awarded.",
                db=db
            )
            credits_awarded = reward_res.get("awarded_amount", 5)
        except Exception as exc:
            log.error("Failed to disburse credit reward: %s", exc)

    return {
        "status": "success",
        "message": f"Report #{report_id} verified. '{report.scammer_entity}' added to Global Blacklist. +{credits_awarded} credits awarded to reporting candidate.",
        "report_id": report_id,
        "credits_awarded": credits_awarded,
    }


@router.post("/admin/reports/{report_id}/dismiss")
async def dismiss_scam_report(
    report_id: int,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Dismiss a benign or invalid scam report."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")

    report = db.query(models.ScamReport).filter(models.ScamReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Scam report not found")

    report.status = "DISMISSED"
    db.add(report)
    db.commit()

    return {
        "status": "success",
        "message": f"Report #{report_id} marked as dismissed.",
        "report_id": report_id,
    }


# ── Global Blacklist Registry Endpoints ──────────────────────────────────────

@router.get("/admin/blacklist")
async def get_blacklist_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Return all blacklisted companies and suspicious domains."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    return {
        "status": "success",
        "blacklist": scam_service.get_blacklist(),
        "total": len(scam_service.get_blacklist())
    }


@router.post("/admin/blacklist/add")
async def add_manual_blacklist_endpoint(
    payload: AddBlacklistRequest,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Manually register a company or domain into the Global Blacklist."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    entry = scam_service.add_to_blacklist(
        company_name=payload.company_name,
        domain=payload.domain,
        scam_pattern=payload.scam_pattern,
        severity=payload.severity or "CRITICAL"
    )
    return {"status": "success", "message": f"'{payload.company_name}' added to Global Blacklist.", "entry": entry}


@router.delete("/admin/blacklist/{company_id}")
async def delete_blacklist_endpoint(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Remove a company from the Global Blacklist."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    removed = scam_service.remove_from_blacklist(company_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Blacklist entry not found")
    return {"status": "success", "message": f"Blacklist entry #{company_id} unblocked."}


# ── User Permissions Matrix Endpoints ────────────────────────────────────────

@router.get("/admin/users")
async def list_admin_users_matrix(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Retrieve full user matrix with tier status and credits for permissions management."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    users = UserService.get_all_users_matrix(db)
    return {"status": "success", "users": users, "total": len(users)}


@router.post("/admin/users/{user_id}/toggle-premium")
async def toggle_user_premium_endpoint(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Toggle user between Free and Premium tiers."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    return UserService.toggle_user_premium(user_id, db)


@router.post("/admin/users/{user_id}/credits")
async def adjust_user_credits_endpoint(
    user_id: int,
    payload: CreditAdjustmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Manually add or adjust credits for a user."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    return UserService.adjust_user_credits(user_id, payload.credits_to_add, db)


@router.post("/admin/users/{user_id}/toggle-ban")
async def toggle_user_ban_endpoint(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Toggle account active/banned status."""
    admin_user = _extract_optional_user(request, db, authorization)
    if not admin_user or (admin_user.role or "").upper() != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    return UserService.toggle_user_ban(user_id, db)


# ── Analytics Visualizations ─────────────────────────────────────────────────

@router.get("/analytics/charts")
async def get_visual_analytics_charts() -> dict[str, str]:
    """Stream base64-encoded Matplotlib visual analytics graphs."""
    return analytics_service.get_all_charts_base64()


# ── Multi-Input Scam Detector Endpoints ──────────────────────────────────────

@router.post("/scam/check")
async def check_scam(payload: ScamCheckRequest) -> dict[str, Any]:
    """Analyze plain offer text or message content for fraud indicators & blacklist matches."""
    return scam_service.analyze_text(
        text=payload.content or "",
        entity_name=payload.company_name
    )


@router.post("/scam/analyze-doc")
async def analyze_scam_document(
    file: UploadFile = File(...),
    entity_name: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Extract and analyze uploaded PDF or DOCX offer letter for fee demands."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".doc", ".txt"}:
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, and TXT offer documents are supported.")

    max_bytes = 10 * 1024 * 1024
    desc, temp_path = tempfile.mkstemp(prefix="scam_doc_", suffix=suffix)
    try:
        with os.fdopen(desc, "wb") as fh:
            total = 0
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="Offer document exceeds the 10 MB upload limit.")
                fh.write(chunk)
        return scam_service.analyze_document(temp_path, entity_name=entity_name)
    finally:
        await file.close()
        Path(temp_path).unlink(missing_ok=True)


@router.post("/scam/analyze-url")
async def analyze_scam_url(payload: ScamUrlRequest) -> dict[str, Any]:
    """Inspect recruiter / company website URL for domain security risks & blacklist matches."""
    return scam_service.analyze_url(payload.url)


@router.post("/scam/report")
async def report_scam(
    payload: ScamReportCreate,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Queue candidate fraud intelligence for admin review and credit disbursement."""
    user = _extract_optional_user(request, db, authorization)
    user_id = user.id if user else None
    
    report = models.ScamReport(
        user_id=user_id,
        scammer_entity=payload.scammer_entity,
        platform=payload.platform,
        scam_type=payload.scam_type,
        description=payload.description,
        evidence_url=payload.evidence_url,
        status="PENDING",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return {
        "status": "success",
        "message": "Scam report queued for admin review. You will receive +5 Credits upon confirmation.",
        "report_id": report.id,
    }


# ── Institute Search Endpoints ───────────────────────────────────────────────

@router.get("/institute/search")
async def search_institute(
    q: Optional[str] = Query(default=None, max_length=200),
    city: Optional[str] = Query(default=None, max_length=100),
    status: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    from app import INSTITUTE_DB

    q_clean = (q or "").strip().lower()
    city_clean = (city or "").strip().lower()
    status_clean = (status or "").strip().lower()

    if q_clean.isdigit():
        zcode = q_clean.zfill(5)
        record = INSTITUTE_DB.get(zcode) or INSTITUTE_DB.get(q_clean)
        if record:
            detail = dict(record)
            detail.setdefault("dte_code", zcode)
            return {"match_type": "exact", "detail": detail, "total_matches": 1, "limit": limit, "offset": offset}

    hits: list[dict[str, Any]] = []
    for dte_code, record in INSTITUTE_DB.items():
        if not isinstance(record, dict):
            continue
        location = str(record.get("city") or record.get("location") or "")
        record_status = str(record.get("status") or (record.get("system_overview") or {}).get("status") or "")
        college_name = str(record.get("name") or "")

        if city_clean and city_clean != "all" and city_clean not in location.lower():
            continue
        if status_clean and status_clean != "all" and status_clean not in record_status.lower():
            continue
        if q_clean and q_clean not in college_name.lower() and q_clean not in location.lower() and q_clean not in str(dte_code).lower():
            continue

        hit = dict(record)
        hit["dte_code"] = str(dte_code).zfill(5)
        hits.append(hit)

    total_matches = len(hits)
    page_hits = hits[offset: offset + limit]

    if total_matches == 0:
        return {"match_type": "none", "total_matches": 0, "suggestions": [], "limit": limit, "offset": offset}
    if total_matches == 1:
        return {"match_type": "exact", "detail": page_hits[0], "total_matches": 1, "limit": limit, "offset": offset}
    return {
        "match_type": "suggestions",
        "suggestions": page_hits,
        "total_matches": total_matches,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total_matches,
    }



@router.get("/institute/detail/{code}")
async def get_institute_detail(code: str) -> dict[str, Any]:
    from app import INSTITUTE_DB
    
    clean_code = str(code).strip()
    zcode = clean_code.zfill(5)
    
    record = INSTITUTE_DB.get(zcode) or INSTITUTE_DB.get(clean_code)
    if not record:
        return {"status": "error", "message": f"Institute with DTE Code {clean_code} not found"}
    
    detail = dict(record)
    detail.setdefault("dte_code", zcode)
    return {"status": "success", "detail": detail}


@router.get("/institute/filters")
async def get_filters() -> dict[str, list[str]]:
    from app import INSTITUTE_DB

    cities = sorted({
        str(record.get("city") or record.get("location") or "").split(",")[0].strip()
        for record in INSTITUTE_DB.values()
        if isinstance(record, dict) and (record.get("city") or record.get("location"))
    } - {""})
    
    statuses = sorted({
        str(record.get("status") or (record.get("system_overview") or {}).get("status") or "").strip()
        for record in INSTITUTE_DB.values()
        if isinstance(record, dict)
    } - {""})
    
    return {"cities": cities, "statuses": statuses}


# ── Jobs Recommender Endpoints ───────────────────────────────────────────────

@router.post("/jobs/recommend")
async def recommend_jobs(payload: JobRecommendRequest) -> dict[str, Any]:
    skills = payload.skills or ["python", "software engineer", "developer"]
    return job_service.match_jobs(
        candidate_skills=skills,
        target_role=payload.role,
        limit=payload.limit,
        offset=payload.offset,
    )


@router.get("/jobs/recommend")
async def recommend_jobs_get(
    skills: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    limit: int = Query(default=18, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    skill_list = [s.strip() for s in (skills or "python,developer").split(",") if s.strip()]
    return job_service.match_jobs(
        candidate_skills=skill_list,
        target_role=role,
        limit=limit,
        offset=offset,
    )


# ── Dual-Pathway Predictor Endpoints ─────────────────────────────────────────

@router.post("/predict")
async def predict_dual_pathway(
    req: PredictRequest,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Unified endpoint serving both MHT-CET (FE) and Diploma (DSE) predictions."""
    score = req.percentile if req.percentile is not None else req.percentage
    if score is None:
        raise HTTPException(status_code=400, detail="Score (percentile or percentage) is required.")
    
    resp = cet_engine.predict(
        percentile=score,
        category=req.category,
        eligible_for_ladies=req.eligible_for_ladies,
        special_eligibilities=req.special_eligibilities,
        university_scope=req.university_scope,
        branch=req.branch,
        city=req.city,
        college_type=req.college_type,
        extra_filters=req.extra_filters,
        pathway=req.pathway,
    )
    
    flat_results = resp.get("results", []) if isinstance(resp, dict) else resp

    user = _extract_optional_user(request, db, authorization)
    if user:
        try:
            pred_record = models.Prediction(
                user_id=user.id,
                score=score,
                exam_type=str(req.pathway or "fe").upper(),
                category=req.category or "OPEN",
                branch_preference=req.branch or "All",
                filters=req.extra_filters or {},
                results=flat_results,
            )
            db.add(pred_record)
            db.commit()
        except Exception as exc:
            db.rollback()
            log.warning("Could not persist prediction history: %s", exc)

    return resp if isinstance(resp, dict) else {"results": flat_results, "total": len(flat_results), "pathway": req.pathway, "status": "success"}


@router.post("/predict/cet")
async def predict_cet_endpoint(
    req: CETRequestLegacy,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    resp = cet_engine.predict(
        percentile=req.percentile,
        category=req.category,
        eligible_for_ladies=req.eligible_for_ladies,
        special_eligibilities=req.special_eligibilities,
        university_scope=req.university_scope,
        branch=req.branch,
        city=req.city,
        college_type=req.college_type,
        extra_filters=req.extra_filters,
        pathway="fe",
    )
    flat_results = resp.get("results", []) if isinstance(resp, dict) else resp
    return resp if isinstance(resp, dict) else {"results": flat_results, "total": len(flat_results), "status": "success"}


@router.post("/predict/diploma")
async def predict_diploma_endpoint(
    req: DiplomaRequestLegacy,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    resp = diploma_engine.predict(
        percentage=req.percentage,
        category=req.category,
        eligible_for_ladies=req.eligible_for_ladies,
        special_eligibilities=req.special_eligibilities,
        university_scope=req.university_scope,
        branch=req.branch,
        city=req.city,
        college_type=req.college_type,
        extra_filters=req.extra_filters,
    )
    flat_results = resp.get("results", []) if isinstance(resp, dict) else resp
    return resp if isinstance(resp, dict) else {"results": flat_results, "total": len(flat_results), "status": "success"}


@router.get("/predict/filters")
async def get_predictor_filters_route(pathway: Optional[str] = Query(default=None)) -> dict[str, Any]:
    filters = data_loader.get_unique_filters(pathway=pathway)
    filters["student_categories"] = student_category_options(data_loader.get_data_by_pathway(pathway or "fe"))
    return filters


@router.get("/predict/trends")
async def get_trends():
    return {
        "branch_trends": trends_engine.get_branch_trends()[:10],
        "city_trends": trends_engine.get_city_trends()[:10],
        "competition": trends_engine.get_competition_analysis()[:10],
    }


# ── Prediction Audit Report Exports ──────────────────────────────────────────

def _resolve_report_data(
    percentile: Optional[float],
    percentage: Optional[float],
    score: Optional[float],
    category: Optional[str],
    branch: Optional[str],
    city: Optional[str],
    college_type: Optional[str],
    pathway: Optional[str],
    eligible_for_ladies: bool,
    special_eligibilities: List[str],
    university_scope: Optional[str],
    results_override: Optional[List[Dict[str, Any]]],
    request: Request,
    db: Session,
    authorization: Optional[str],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    effective_score = percentile if percentile is not None else (percentage if percentage is not None else score)
    if effective_score is None:
        effective_score = 85.0
    effective_pathway = (pathway or "fe").lower()
    
    if results_override and isinstance(results_override, list) and len(results_override) > 0:
        results = results_override
    else:
        results = cet_engine.predict(
            percentile=effective_score,
            category=category or "OPEN",
            eligible_for_ladies=eligible_for_ladies,
            special_eligibilities=special_eligibilities,
            university_scope=university_scope,
            branch=branch,
            city=city,
            college_type=college_type,
            pathway=effective_pathway,
        )

    user = _extract_optional_user(request, db, authorization)
    meta = {
        "score": effective_score,
        "category": category or "OPEN",
        "branch": branch or "All Branches",
        "city": city or "All Cities",
        "pathway": effective_pathway.upper(),
        "generated_by": user.email if user else "Anonymous Agent",
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    return results, meta


@router.get("/predict/report/{format}")
async def export_prediction_report_get(
    format: str,
    request: Request,
    percentile: Optional[float] = Query(default=None),
    percentage: Optional[float] = Query(default=None),
    score: Optional[float] = Query(default=None),
    category: Optional[str] = Query(default="OPEN"),
    branch: Optional[str] = Query(default=None),
    city: Optional[str] = Query(default=None),
    college_type: Optional[str] = Query(default=None),
    eligible_for_ladies: bool = Query(default=False),
    special_eligibilities: List[str] = Query(default=[]),
    university_scope: Optional[str] = Query(default="ANY"),
    pathway: Optional[str] = Query(default="fe"),
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> Response:
    """Download prediction audit report (PDF, CSV, EXCEL, JSON) via GET query parameters."""
    results, meta = _resolve_report_data(
        percentile=percentile, percentage=percentage, score=score, category=category,
        branch=branch, city=city, college_type=college_type, pathway=pathway,
        eligible_for_ladies=eligible_for_ladies, special_eligibilities=special_eligibilities, university_scope=university_scope,
        results_override=None, request=request, db=db, authorization=authorization,
    )
    
    content_bytes, media_type, filename = generate_report(results, format_type=format, metadata=meta)
    
    return Response(
        content=content_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post("/predict/report/{format}")
async def export_prediction_report_post(
    format: str,
    payload: ReportExportPayload,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> Response:
    """Download prediction audit report (PDF, CSV, EXCEL, JSON) via POST JSON payload."""
    results, meta = _resolve_report_data(
        percentile=payload.percentile, percentage=payload.percentage, score=payload.score,
        category=payload.category, branch=payload.branch, city=payload.city,
        college_type=payload.college_type, pathway=payload.pathway,
        eligible_for_ladies=payload.eligible_for_ladies, special_eligibilities=payload.special_eligibilities, university_scope=payload.university_scope,
        results_override=payload.results, request=request, db=db, authorization=authorization,
    )
    
    content_bytes, media_type, filename = generate_report(results, format_type=format, metadata=meta)
    
    return Response(
        content=content_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


# ── RLHF Chatbot Endpoints ───────────────────────────────────────────────────

@router.post("/chat/query")
async def chat_query_endpoint(req: ChatQueryRequest) -> dict[str, Any]:
    return rlhf_assistant.generate_response(req.query)


@router.post("/chat/feedback")
async def chat_feedback_endpoint(req: ChatFeedbackRequest) -> dict[str, Any]:
    return rlhf_assistant.record_feedback(req.message_id, req.score)


@router.get("/chat/feedback/stats")
async def chat_feedback_stats() -> dict[str, Any]:
    return rlhf_assistant.get_feedback_summary()


# ── User Profile & Account Settings Endpoints ────────────────────────────────

@router.get("/user/profile")
async def get_user_profile(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to view profile.")
    settings: dict = user.profile_settings or {}
    is_admin = (user.role or "").upper() == "ADMIN"
    is_premium = bool(settings.get("is_premium", False) or is_admin)
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "is_admin": is_admin,
        "is_premium": is_premium,
        "tier": "PREMIUM" if is_premium else "FREE",
        "career_interests": user.career_interests or [],
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "roll_number": settings.get("roll_number", ""),
        "preferred_branch": settings.get("preferred_branch", ""),
        "category": settings.get("category", ""),
        "target_city": settings.get("target_city", ""),
        "saved_colleges": settings.get("saved_colleges", []),
        "saved_jobs": settings.get("saved_jobs", []),
        "free_pdf_downloads": settings.get("free_pdf_downloads", 9999 if is_premium else 3),
        "credits": settings.get("credits", 9999 if is_premium else 0),
        "avatar_url": settings.get("avatar_url"),
    }


@router.post("/user/profile")
async def update_user_profile(
    payload: ProfileUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if payload.full_name:
        user.full_name = payload.full_name.strip()
    settings: dict = dict(user.profile_settings or {})
    if payload.roll_number is not None:
        settings["roll_number"] = payload.roll_number.strip()
    if payload.preferred_branch is not None:
        settings["preferred_branch"] = payload.preferred_branch.strip()
    if payload.category is not None:
        settings["category"] = payload.category.strip()
    if payload.target_city is not None:
        settings["target_city"] = payload.target_city.strip()
    user.profile_settings = settings
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except Exception as exc:
        db.rollback()
        log.error("Profile update DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save profile.")
    return {"status": "success", "message": "Profile updated successfully."}


@router.post("/user/profile/avatar")
async def upload_profile_avatar(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, str]:
    """Store a bounded, signature-validated avatar for the current user only."""
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    received = bytearray()
    try:
        while chunk := await file.read(256 * 1024):
            received.extend(chunk)
            if len(received) > _MAX_AVATAR_BYTES:
                raise HTTPException(status_code=413, detail="Profile image exceeds the 2 MB limit.")
        extension = _avatar_extension(bytes(received))
        if not extension:
            raise HTTPException(status_code=400, detail="Use a PNG, JPEG, or WebP image.")
        _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"user-{user.id}-{uuid.uuid4().hex}{extension}"
        destination = _AVATAR_DIR / filename
        destination.write_bytes(received)
        settings = dict(user.profile_settings or {})
        old_url = settings.get("avatar_url")
        settings["avatar_url"] = f"/static/uploads/avatars/{filename}"
        user.profile_settings = settings
        db.add(user)
        db.commit()
        _remove_avatar(old_url)
        return {"status": "success", "avatar_url": settings["avatar_url"]}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        log.exception("Profile avatar upload failed")
        raise HTTPException(status_code=500, detail="Unable to save profile image.") from exc
    finally:
        await file.close()


@router.delete("/user/profile/avatar")
async def delete_profile_avatar(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, str]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    settings = dict(user.profile_settings or {})
    old_url = settings.pop("avatar_url", None)
    user.profile_settings = settings
    db.add(user)
    db.commit()
    _remove_avatar(old_url)
    return {"status": "removed"}


@router.post("/user/settings")
async def change_user_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    from auth_service import get_password_hash, verify_password
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    user.hashed_password = get_password_hash(payload.new_password)
    try:
        db.add(user)
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("Password change DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update password.")
    return {"status": "success", "message": "Password changed successfully."}


@router.get("/user/bookmarks")
async def get_user_bookmarks(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        return {"saved_colleges": [], "saved_jobs": []}
    settings: dict = user.profile_settings or {}
    return {
        "saved_colleges": settings.get("saved_colleges", []),
        "saved_jobs": settings.get("saved_jobs", []),
    }


@router.post("/user/bookmarks/college")
async def bookmark_college(
    payload: BookmarkCollegeRequest,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    settings: dict = dict(user.profile_settings or {})
    colleges: list = list(settings.get("saved_colleges", []))
    dte = payload.dte_code.strip().zfill(5)
    if not any(c.get("dte_code") == dte for c in colleges):
        colleges.append({"dte_code": dte, "name": payload.name or "", "city": payload.city or ""})
        settings["saved_colleges"] = colleges
        user.profile_settings = settings
        db.add(user)
        db.commit()
    return {"status": "success", "saved_colleges": colleges}


@router.delete("/user/bookmarks/college/{dte_code}")
async def remove_college_bookmark(
    dte_code: str,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    settings: dict = dict(user.profile_settings or {})
    colleges = [c for c in settings.get("saved_colleges", []) if c.get("dte_code") != dte_code.zfill(5)]
    settings["saved_colleges"] = colleges
    user.profile_settings = settings
    db.add(user)
    db.commit()
    return {"status": "success", "saved_colleges": colleges}


@router.post("/user/bookmarks/job")
async def bookmark_job(
    payload: BookmarkJobRequest,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    settings: dict = dict(user.profile_settings or {})
    jobs: list = list(settings.get("saved_jobs", []))
    if not any(j.get("job_id") == payload.job_id for j in jobs):
        jobs.append({"job_id": payload.job_id, "title": payload.title or "", "company": payload.company or ""})
        settings["saved_jobs"] = jobs
        user.profile_settings = settings
        db.add(user)
        db.commit()
    return {"status": "success", "saved_jobs": jobs}


@router.delete("/user/bookmarks/job/{job_id}")
async def remove_job_bookmark(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    user = _extract_optional_user(request, db, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    settings: dict = dict(user.profile_settings or {})
    jobs = [j for j in settings.get("saved_jobs", []) if j.get("job_id") != job_id]
    settings["saved_jobs"] = jobs
    user.profile_settings = settings
    db.add(user)
    db.commit()
    return {"status": "success", "saved_jobs": jobs}


# ── Clean Dropdown Options Endpoints ──────────────────────────────────────────

@router.get("/options/cities")
async def get_options_cities() -> list[str]:
    """Return pristine, sorted list of Maharashtra administrative districts."""
    df = data_loader.get_all_data()
    return sorted(df["city"].dropna().unique().tolist())


@router.get("/options/branches")
async def get_options_branches() -> list[str]:
    """Return pristine, sorted list of normalized engineering discipline branches."""
    df = data_loader.get_all_data()
    return sorted(df["branch"].dropna().unique().tolist())


@router.get("/options/categories")
async def get_options_categories() -> list[str]:
    """Return pristine, sorted list of standard admission categories and seat codes."""
    filters = data_loader.get_unique_filters()
    return filters.get("categories", [])


@router.get("/options/college-types")
async def get_options_college_types() -> list[str]:
    """Return pristine, sorted list of standardized college types."""
    df = data_loader.get_all_data()
    return sorted(df["college_type"].dropna().unique().tolist())
