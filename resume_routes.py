"""Authenticated, bounded resume-analysis and tailoring API routes with graceful session fallback."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import models
from auth_routes import COOKIE_NAME
from auth_service import get_token_payload
from database import get_db
from resume_service import DocumentValidationError, MAX_FILE_BYTES, ResumeService

log = logging.getLogger("digipath.resume_routes")

router = APIRouter(prefix="/resume", tags=["Resume AI"])
resume_service = ResumeService()
_ALLOWED_SUFFIXES = {".pdf", ".docx"}
_CHUNK_BYTES = 1024 * 1024


class ResumeTailorPayload(BaseModel):
    resume_text: str = Field(min_length=10, max_length=500000)
    job_description: str = Field(min_length=10, max_length=500000)


def _extract_optional_user(
    request: Request,
    db: Session,
    authorization: Optional[str] = None,
) -> Optional[models.User]:
    """Extract authenticated database user from Bearer header or cookie with graceful fallback."""
    token: Optional[str] = None

    # 1. Check Authorization header
    if authorization:
        scheme, _, bearer_token = authorization.partition(" ")
        if scheme.lower() == "bearer" and bearer_token.strip():
            token = bearer_token.strip()
        elif authorization.strip() and not bearer_token:
            token = authorization.strip()

    # 2. Check HTTP cookie if header absent
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


async def _save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix.casefold()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF and DOCX resumes are supported.")
    descriptor, temporary_name = tempfile.mkstemp(prefix="digipath_resume_", suffix=suffix)
    total_size = 0
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            while True:
                chunk = await upload.read(_CHUNK_BYTES)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_BYTES:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Document exceeds the 10 MB upload limit.")
                temporary_file.write(chunk)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return Path(temporary_name)


@router.post("/analyze")
async def analyze_resume(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    current_user = _extract_optional_user(request, db, authorization)
    user_interests = (current_user.career_interests or []) if current_user else []

    temporary_path = await _save_upload(file)
    if current_user:
        from utils.credits import deduct_user_credits, COST_RESUME_ATS
        deduct_user_credits(db, current_user.id, COST_RESUME_ATS)

    try:
        analysis = resume_service.analyze(temporary_path, interests=user_interests)

        # If user is authenticated, save record to history
        if current_user:
            try:
                record = models.ResumeAnalysis(
                    user_id=current_user.id,
                    filename=Path(file.filename or "resume").name,
                    ats_score=analysis["ats_score"],
                    skill_score=analysis["skill_score"],
                    project_score=analysis["project_score"],
                    keyword_score=analysis["keyword_score"],
                    education_score=analysis["education_score"],
                    extracted_skills=analysis["extracted_skills"],
                    domain=analysis["domain"],
                    improvement_suggestions=analysis["improvement_suggestions"],
                    recommended_careers=analysis["recommended_careers"],
                    recommended_job_roles=analysis["recommended_job_roles"],
                    recommended_certifications=analysis["recommended_certifications"],
                    recommended_higher_studies=analysis["recommended_higher_studies"],
                    skill_gaps=analysis["skill_gaps"],
                    learning_roadmap=analysis["learning_roadmap"],
                    industry_recommendations=analysis["industry_recommendations"],
                )
                db.add(record)
                db.commit()
                db.refresh(record)
            except SQLAlchemyError as exc:
                db.rollback()
                log.warning("Resume analysis could not be persisted to DB: %s", exc)

        return analysis
    except DocumentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Unexpected error in resume analyzer: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Analysis failed: {str(exc)}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


@router.post("/tailor")
async def tailor_resume_endpoint(
    payload: ResumeTailorPayload,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict[str, Any]:
    """Tailor resume text against a target job description."""
    try:
        return resume_service.tailor_resume(
            resume_text=payload.resume_text,
            job_description=payload.job_description
        )
    except Exception as exc:
        log.exception("Resume tailoring failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Job tailoring failed: {str(exc)}"
        ) from exc


@router.get("/history")
async def get_resume_history(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[Optional[str], Header()] = None,
) -> list[dict[str, Any]]:
    current_user = _extract_optional_user(request, db, authorization)
    if not current_user:
        return []
    records = db.query(models.ResumeAnalysis).filter(models.ResumeAnalysis.user_id == current_user.id).order_by(models.ResumeAnalysis.created_at.desc()).all()
    return [
        {
            "id": record.id,
            "filename": record.filename,
            "ats_score": record.ats_score,
            "domain": record.domain,
            "skills_detected": record.extracted_skills or [],
            "recommended_roles": record.recommended_job_roles or [],
            "improvement_suggestions": record.improvement_suggestions or [],
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }
        for record in records
    ]
