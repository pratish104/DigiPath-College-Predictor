from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.orm import Session
from database import get_db
import models
from resume_service import ResumeService
from data_loader import DataLoader
import os
import shutil

router = APIRouter(prefix="/resume", tags=["Resume AI"])

# Initialize Service
base_path = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(base_path, "data")
inst_json = os.path.join(base_path, "institutes.json")
loader = DataLoader(data_dir, inst_json)
resume_service = ResumeService(loader)

# Helper to get current user from cookies
async def get_current_user_id(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "):
        return None
    
    from auth_service import decode_access_token
    payload = decode_access_token(token.split(" ")[1])
    if not payload:
        return None
        
    email = payload.get("sub")
    user = db.query(models.User).filter(models.User.email == email).first()
    return user.id if user else None

@router.post("/analyze")
async def analyze_resume(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required for resume analysis")

    # Save temp file
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # Get user interests
        user = db.query(models.User).filter(models.User.id == user_id).first()
        interests = user.career_interests if user else []

        analysis = resume_service.analyze(temp_path, interests)
        
        # Store in DB
        db_analysis = models.ResumeAnalysis(
            user_id=user_id,
            filename=file.filename,
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
            industry_recommendations=analysis["industry_recommendations"]
        )
        db.add(db_analysis)
        db.commit()
        db.refresh(db_analysis)

        return analysis
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@router.get("/history")
async def get_resume_history(request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    return db.query(models.ResumeAnalysis).filter(models.ResumeAnalysis.user_id == user_id).order_by(models.ResumeAnalysis.created_at.desc()).all()
