"""DigiPath Admission Predictor Routes.

Provides deterministic, high-performance college cutoff predictions for:
  - MHT-CET (First Year Engineering / FE)
  - Diploma / DSE (Direct Second Year Engineering)

Features:
  - Rate limiting (10 req/min) via slowapi
  - Credit wallet integration (5 credits deducted per prediction)
  - System error auditing
  - Report downloads (PDF, CSV, Excel)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
import pandas as pd
from typing import Optional
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cet_predictor import CETPredictor, DiplomaPredictor
from data_loader import DataLoader
from database import get_db
import models
from prediction_service import PredictionService
from trend_analyzer import TrendAnalyzer
from utils.credits import COST_PREDICTOR, deduct_user_credits
from utils.error_audit import log_system_error

log = logging.getLogger("digipath.predictor_routes")
router = APIRouter(tags=["Predictor"])

# ── Components Initialization ──────────────────────────────────────────────────
base_path = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(base_path, "data")
inst_json = os.path.join(base_path, "institutes.json")

loader = DataLoader(data_dir, inst_json)
cet_engine = CETPredictor(loader)
diploma_engine = DiplomaPredictor(loader)
trends_engine = TrendAnalyzer(loader)


def _get_limiter():
    try:
        from app import limiter
        return limiter
    except Exception:
        return None


# ── Pydantic Request Schemas ──────────────────────────────────────────────────
class CETRequest(BaseModel):
    percentile: float = Field(..., ge=0.0, le=100.0)
    category: str = "OPEN"
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    seat_type: Optional[str] = None
    eligible_for_ladies: bool = False
    special_eligibilities: List[str] = Field(default_factory=list)
    university_scope: Optional[str] = "ANY"
    quota: Optional[str] = None
    year: Optional[int] = Field(default=None, ge=2000, le=2100)
    round_name: Optional[str] = None
    extra_filters: Optional[dict] = None


class DiplomaRequest(BaseModel):
    percentage: float = Field(..., ge=0.0, le=100.0)
    category: str = "OPEN"
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    seat_type: Optional[str] = None
    eligible_for_ladies: bool = False
    special_eligibilities: List[str] = Field(default_factory=list)
    university_scope: Optional[str] = "ANY"
    quota: Optional[str] = None
    year: Optional[int] = Field(default=None, ge=2000, le=2100)
    round_name: Optional[str] = None
    extra_filters: Optional[dict] = None


class UnifiedPredictRequest(BaseModel):
    score: Optional[float] = None
    percentile: Optional[float] = None
    percentage: Optional[float] = None
    category: str = "OPEN"
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    seat_type: Optional[str] = None
    eligible_for_ladies: bool = False
    special_eligibilities: List[str] = Field(default_factory=list)
    university_scope: Optional[str] = "ANY"
    quota: Optional[str] = None
    exam_type: Optional[str] = "CET"
    pathway: Optional[str] = "fe"
    year: Optional[int] = Field(default=None, ge=2000, le=2100)
    round_name: Optional[str] = None
    extra_filters: Optional[dict] = None


class InterestUpdate(BaseModel):
    interests: List[str]


# ── Auth Helper ───────────────────────────────────────────────────────────────
async def get_current_user_id(request: Request, db: Session) -> Optional[int]:
    """Resolve current user ID from Authorization header or cookie without raising."""
    token: Optional[str] = None
    auth_hdr = request.headers.get("Authorization")
    if auth_hdr:
        scheme, _, bearer = auth_hdr.partition(" ")
        if scheme.lower() == "bearer" and bearer.strip():
            token = bearer.strip()

    if not token:
        cookie_val = request.cookies.get("access_token", "").strip()
        if cookie_val:
            scheme, _, cookie_tok = cookie_val.partition(" ")
            if scheme.lower() == "bearer" and cookie_tok.strip():
                token = cookie_tok.strip()
            elif cookie_val and not cookie_tok:
                token = cookie_val

    if not token:
        return None

    try:
        from auth_service import get_token_payload
        payload = get_token_payload(token)
        if not payload or not payload.get("sub"):
            return None
        email = str(payload["sub"]).strip().lower()
        user = db.query(models.User).filter(models.User.email == email).first()
        return user.id if user else None
    except Exception:
        return None


# ── 1. CET Prediction Endpoint ────────────────────────────────────────────────
@router.post("/predict/cet")
async def predict_cet(
    req: CETRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    MHT-CET First Year Engineering Admission Predictor.
    Rate limited: 10 req/min.
    Deducts 5 credits if authenticated.
    """
    limiter = _get_limiter()
    if limiter:
        try:
            await limiter._check_request_limit(
                request,
                endpoint_func=predict_cet,
                rate_limit="10/minute",
                is_async=True,
            )
        except Exception:
            pass

    try:
        log.info("CET API Called: percentile=%.2f, category=%s", req.percentile, req.category)

        user_id = await get_current_user_id(request, db)
        if user_id:
            deduct_user_credits(db, user_id, COST_PREDICTOR)

        resp = cet_engine.predict(
            percentile=req.percentile,
            category=req.category,
            seat_code=req.seat_type,
            eligible_for_ladies=req.eligible_for_ladies,
            special_eligibilities=req.special_eligibilities,
            university_scope=req.university_scope,
            branch=req.branch,
            city=req.city,
            college_type=req.college_type,
            year=req.year,
            round_name=req.round_name,
            extra_filters=req.extra_filters,
        )

        flat_results = resp.get("results", []) if isinstance(resp, dict) else resp
        log.info("CET Prediction completed: %d results", len(flat_results))

        if user_id:
            try:
                PredictionService.save_prediction(
                    db, user_id, req.percentile, "CET", req.category, req.branch or "All", flat_results
                )
            except Exception as exc:
                log.warning("Could not persist CET prediction: %s", exc)

        return resp if isinstance(resp, dict) else {"results": flat_results, "total": len(flat_results), "status": "success"}

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("CET API Error: %s", exc)
        uid = await get_current_user_id(request, db)
        log_system_error(db, "PREDICTOR", str(exc), user_id=uid)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prediction failed: {exc}",
        ) from exc


# ── 2. Diploma Prediction Endpoint ────────────────────────────────────────────
@router.post("/predict/diploma")
async def predict_diploma(
    req: DiplomaRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Direct Second Year (DSE) Diploma Admission Predictor.
    Rate limited: 10 req/min.
    Deducts 5 credits if authenticated.
    """
    limiter = _get_limiter()
    if limiter:
        try:
            await limiter._check_request_limit(
                request,
                endpoint_func=predict_diploma,
                rate_limit="10/minute",
                is_async=True,
            )
        except Exception:
            pass

    try:
        log.info("Diploma API Called: percentage=%.2f, category=%s", req.percentage, req.category)

        user_id = await get_current_user_id(request, db)
        if user_id:
            deduct_user_credits(db, user_id, COST_PREDICTOR)

        resp = diploma_engine.predict(
            percentage=req.percentage,
            category=req.category,
            seat_code=req.seat_type,
            eligible_for_ladies=req.eligible_for_ladies,
            special_eligibilities=req.special_eligibilities,
            university_scope=req.university_scope,
            branch=req.branch,
            city=req.city,
            college_type=req.college_type,
            year=req.year,
            round_name=req.round_name,
            extra_filters=req.extra_filters,
        )

        flat_results = resp.get("results", []) if isinstance(resp, dict) else resp
        log.info("Diploma Prediction completed: %d results", len(flat_results))

        if user_id:
            try:
                PredictionService.save_prediction(
                    db, user_id, req.percentage, "DIPLOMA", req.category, req.branch or "All", flat_results
                )
            except Exception as exc:
                log.warning("Could not persist Diploma prediction: %s", exc)

        return resp if isinstance(resp, dict) else {"results": flat_results, "total": len(flat_results), "status": "success"}

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Diploma API Error: %s", exc)
        uid = await get_current_user_id(request, db)
        log_system_error(db, "PREDICTOR", str(exc), user_id=uid)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prediction failed: {exc}",
        ) from exc


# ── 3. Unified Predict & Predict/ML Endpoint Aliases ───────────────────────────
@router.post("/predict")
@router.post("/predict/ml")
async def predict_unified(
    req: UnifiedPredictRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Unified predictor supporting both CET and Diploma with rate-limiting & wallet deductions.
    """
    limiter = _get_limiter()
    if limiter:
        try:
            await limiter._check_request_limit(
                request,
                endpoint_func=predict_unified,
                rate_limit="10/minute",
                is_async=True,
            )
        except Exception:
            pass

    score = req.percentile if req.percentile is not None else (req.percentage if req.percentage is not None else req.score)
    if score is None:
        raise HTTPException(status_code=400, detail="Score (percentile or percentage) is required.")

    is_diploma = str(req.exam_type or req.pathway or "").upper() in ("DIPLOMA", "DSE")

    user_id = await get_current_user_id(request, db)
    if user_id:
        deduct_user_credits(db, user_id, COST_PREDICTOR)

    try:
        if is_diploma:
            resp = diploma_engine.predict(
                percentage=float(score),
                category=req.category,
                seat_code=req.seat_type,
                eligible_for_ladies=req.eligible_for_ladies,
                special_eligibilities=req.special_eligibilities,
                university_scope=req.university_scope,
                branch=req.branch,
                city=req.city,
                college_type=req.college_type,
                year=req.year,
                round_name=req.round_name,
                extra_filters=req.extra_filters,
            )
            exam_label = "DIPLOMA"
        else:
            resp = cet_engine.predict(
                percentile=float(score),
                category=req.category,
                seat_code=req.seat_type,
                eligible_for_ladies=req.eligible_for_ladies,
                special_eligibilities=req.special_eligibilities,
                university_scope=req.university_scope,
                branch=req.branch,
                city=req.city,
                college_type=req.college_type,
                year=req.year,
                round_name=req.round_name,
                extra_filters=req.extra_filters,
            )
            exam_label = "CET"

        flat_results = resp.get("results", []) if isinstance(resp, dict) else resp

        if user_id:
            try:
                PredictionService.save_prediction(
                    db, user_id, float(score), exam_label, req.category, req.branch or "All", flat_results
                )
            except Exception as exc:
                log.warning("Could not persist unified prediction: %s", exc)

        return resp if isinstance(resp, dict) else {"results": flat_results, "total": len(flat_results), "status": "success", "exam_type": exam_label}

    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Unified Predict error: %s", exc)
        uid = await get_current_user_id(request, db)
        log_system_error(db, "PREDICTOR", str(exc), user_id=uid)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prediction failed: {exc}",
        ) from exc


# ── 4. History, Trends, Filters & Interests ───────────────────────────────────
@router.get("/predict/history")
async def get_history(request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required for history")
    return PredictionService.get_user_history(db, user_id)


@router.get("/predict/trends")
async def get_trends():
    return {
        "branch_trends": trends_engine.get_branch_trends()[:10],
        "city_trends": trends_engine.get_city_trends()[:10],
        "competition": trends_engine.get_competition_analysis()[:10],
    }


@router.get("/predict/filters")
async def get_predictor_filters(pathway: Optional[str] = None):
    return loader.get_unique_filters(pathway=pathway)


@router.get("/options/cities")
async def get_options_cities() -> list[str]:
    """Return pristine, sorted list of Maharashtra administrative districts."""
    df = loader.get_all_data()
    return sorted(df["city"].dropna().unique().tolist())


@router.get("/options/branches")
async def get_options_branches() -> list[str]:
    """Return pristine, sorted list of normalized engineering discipline branches."""
    df = loader.get_all_data()
    return sorted(df["branch"].dropna().unique().tolist())


@router.get("/options/categories")
async def get_options_categories() -> list[str]:
    """Return pristine, sorted list of standard admission categories and seat codes."""
    filters = loader.get_unique_filters()
    return filters.get("categories", [])


@router.get("/options/college-types")
async def get_options_college_types() -> list[str]:
    """Return pristine, sorted list of standardized college types."""
    df = loader.get_all_data()
    return sorted(df["college_type"].dropna().unique().tolist())


@router.get("/user/interests")
async def get_interests(request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    return {"interests": user.career_interests or []}


@router.put("/user/interests")
async def update_interests(req: InterestUpdate, request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    user.career_interests = req.interests
    db.commit()
    return {"message": "Interests updated successfully"}


# ── 5. Report Generation (PDF, CSV, Excel) ────────────────────────────────────
@router.get("/predict/report/{format}")
async def download_report(format: str, request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    last_pred = (
        db.query(models.Prediction)
        .filter(models.Prediction.user_id == user_id)
        .order_by(models.Prediction.created_at.desc())
        .first()
    )
    if not last_pred:
        raise HTTPException(status_code=404, detail="No predictions found for this user")

    results = last_pred.results or []
    df = pd.DataFrame(results)

    report_cols = [
        "rank", "college_name", "branch", "city", "college_type",
        "cutoff", "predicted_cutoff", "probability_percent", "classification",
    ]
    available_cols = [c for c in report_cols if c in df.columns]
    report_df = df[available_cols] if available_cols else df

    static_dir = os.path.join(base_path, "static")
    os.makedirs(static_dir, exist_ok=True)
    filename = f"DigiPath_Report_{user_id}_{int(time.time())}.{format.lower()}"
    file_path = os.path.join(static_dir, filename)

    fmt = format.lower()
    if fmt == "csv":
        report_df.to_csv(file_path, index=False)
    elif fmt == "xlsx":
        report_df.to_excel(file_path, index=False)
    elif fmt == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

        doc = SimpleDocTemplate(file_path, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        elements.append(Paragraph(f"DigiPath Prediction Report - {last_pred.exam_type}", styles["Title"]))
        elements.append(Paragraph(f"<b>User Score:</b> {last_pred.score} | <b>Category:</b> {last_pred.category}", styles["Normal"]))
        elements.append(Paragraph(f"<b>Branch Preference:</b> {last_pred.branch_preference}", styles["Normal"]))
        elements.append(Paragraph(f"<b>Timestamp:</b> {last_pred.created_at}", styles["Normal"]))
        elements.append(Paragraph("<br/><br/>", styles["Normal"]))

        header = ["Rank", "College Name", "Branch", "Cutoff", "Prob %", "Class"]
        data = [header]
        for r in results:
            data.append([
                r.get("rank", "-"),
                str(r.get("college_name", ""))[:45],
                str(r.get("branch", ""))[:25],
                str(r.get("cutoff", "")),
                f"{r.get('probability_percent', '')}%",
                str(r.get("classification", "")),
            ])

        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#00ff41")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elements.append(t)
        doc.build(elements)
    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Choose 'pdf', 'csv', or 'xlsx'.")

    return FileResponse(file_path, filename=filename)
