"""Authenticated, bounded resume-analysis API routes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import models
from auth_routes import get_current_user
from database import get_db
from resume_service import DocumentValidationError, MAX_FILE_BYTES, ResumeService

router = APIRouter(prefix="/resume", tags=["Resume AI"])
resume_service = ResumeService()
_ALLOWED_SUFFIXES = {".pdf", ".docx"}
_CHUNK_BYTES = 1024 * 1024


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
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    temporary_path = await _save_upload(file)
    try:
        analysis = resume_service.analyze(temporary_path, interests=current_user.career_interests or [])
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
        return analysis
    except DocumentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Resume analysis could not be saved.") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


@router.get("/history")
async def get_resume_history(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
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
