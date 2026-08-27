from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from data_loader import DataLoader
from cet_predictor import CETPredictor
from diploma_predictor import DiplomaPredictor
from trend_analyzer import TrendAnalyzer
from prediction_service import PredictionService
from pydantic import BaseModel
from typing import Optional, List
import os
import time
import pandas as pd
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import logging

log = logging.getLogger("digipath.predictor_routes")
router = APIRouter(tags=["Predictor"])

# Initialize components
base_path = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(base_path, "data")
inst_json = os.path.join(base_path, "institutes.json")
loader = DataLoader(data_dir, inst_json)
cet_engine = CETPredictor(loader)
diploma_engine = DiplomaPredictor(loader)
trends_engine = TrendAnalyzer(loader)

class CETRequest(BaseModel):
    percentile: float
    category: str
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    extra_filters: Optional[dict] = None

class DiplomaRequest(BaseModel):
    percentage: float
    category: str
    branch: Optional[str] = None
    city: Optional[str] = None
    college_type: Optional[str] = None
    extra_filters: Optional[dict] = None

# Helper to get current user from cookies (Phase 1 Auth)
async def get_current_user_id(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "):
        return None # Allow guest predictions but won't save history
    
    from auth_service import decode_access_token
    payload = decode_access_token(token.split(" ")[1])
    if not payload:
        return None
        
    email = payload.get("sub")
    import models
    user = db.query(models.User).filter(models.User.email == email).first()
    return user.id if user else None

@router.post("/predict/cet")
async def predict_cet(req: CETRequest, request: Request, db: Session = Depends(get_db)):
    """
    CET Predictor API with full error handling and logging.
    Returns COMPLETE ranked list of matching colleges.
    """
    try:
        log.info(f"🔍 CET API Called: percentile={req.percentile}, category={req.category}")
        
        # Validate request
        if req.percentile is None:
            log.warning("⚠️ Missing percentile")
            raise HTTPException(status_code=400, detail="Percentile is required")
        
        if not req.category:
            log.warning("⚠️ Missing category")
            raise HTTPException(status_code=400, detail="Category is required")
        
        # Call prediction engine
        results = cet_engine.predict(
            percentile=req.percentile,
            category=req.category,
            branch=req.branch,
            city=req.city,
            college_type=req.college_type,
            extra_filters=req.extra_filters
        )
        
        log.info(f"✅ CET Prediction returned {len(results)} results")
        
        # Save prediction if user is authenticated
        user_id = await get_current_user_id(request, db)
        if user_id:
            try:
                PredictionService.save_prediction(
                    db, user_id, req.percentile, "CET", req.category, req.branch or "All", results
                )
                log.info(f"💾 Prediction saved for user {user_id}")
            except Exception as e:
                log.error(f"⚠️ Failed to save prediction: {e}")
                # Don't fail the API call if saving fails
        
        return {"results": results, "total": len(results), "status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"❌ CET API Error: {e}")
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")

@router.post("/predict/diploma")
async def predict_diploma(req: DiplomaRequest, request: Request, db: Session = Depends(get_db)):
    """
    Diploma Predictor API with full error handling and logging.
    Returns COMPLETE ranked list of matching colleges.
    """
    try:
        log.info(f"🔍 Diploma API Called: percentage={req.percentage}, category={req.category}")
        
        # Validate request
        if req.percentage is None:
            log.warning("⚠️ Missing percentage")
            raise HTTPException(status_code=400, detail="Percentage is required")
        
        if not req.category:
            log.warning("⚠️ Missing category")
            raise HTTPException(status_code=400, detail="Category is required")
        
        # Call prediction engine
        results = diploma_engine.predict(
            percentage=req.percentage,
            category=req.category,
            branch=req.branch,
            city=req.city,
            college_type=req.college_type,
            extra_filters=req.extra_filters
        )
        
        log.info(f"✅ Diploma Prediction returned {len(results)} results")
        
        # Save prediction if user is authenticated
        user_id = await get_current_user_id(request, db)
        if user_id:
            try:
                PredictionService.save_prediction(
                    db, user_id, req.percentage, "DIPLOMA", req.category, req.branch or "All", results
                )
                log.info(f"💾 Prediction saved for user {user_id}")
            except Exception as e:
                log.error(f"⚠️ Failed to save prediction: {e}")
                # Don't fail the API call if saving fails
        
        return {"results": results, "total": len(results), "status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"❌ Diploma API Error: {e}")
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")

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
        "competition": trends_engine.get_competition_analysis()[:10]
    }

@router.get("/predict/filters")
async def get_predictor_filters():
    return loader.get_unique_filters()

class InterestUpdate(BaseModel):
    interests: List[str]

@router.get("/user/interests")
async def get_interests(request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    import models
    user = db.query(models.User).filter(models.User.id == user_id).first()
    return {"interests": user.career_interests or []}

@router.put("/user/interests")
async def update_interests(req: InterestUpdate, request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    import models
    user = db.query(models.User).filter(models.User.id == user_id).first()
    user.career_interests = req.interests
    db.commit()
    return {"message": "Interests updated successfully"}

@router.get("/predict/report/{format}")
async def download_report(format: str, request: Request, db: Session = Depends(get_db)):
    user_id = await get_current_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    last_pred = db.query(models.Prediction).filter(models.Prediction.user_id == user_id).order_by(models.Prediction.created_at.desc()).first()
    if not last_pred:
        raise HTTPException(status_code=404, detail="No predictions found")

    results = last_pred.results
    df = pd.DataFrame(results)
    
    # Clean up the dataframe for report
    report_cols = ["rank", "college_name", "branch", "city", "college_type", "cutoff", "predicted_cutoff", "probability_percent", "classification"]
    report_df = df[report_cols] if all(c in df.columns for c in report_cols) else df
    
    filename = f"DigiPath_Report_{user_id}_{int(time.time())}.{format}"
    file_path = os.path.join(base_path, "static", filename)
    
    if format == "csv":
        report_df.to_csv(file_path, index=False)
    elif format == "xlsx":
        report_df.to_excel(file_path, index=False)
    elif format == "pdf":
        doc = SimpleDocTemplate(file_path, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        
        elements.append(Paragraph(f"DigiPath Prediction Report - {last_pred.exam_type}", styles['Title']))
        elements.append(Paragraph(f"<b>User Score:</b> {last_pred.score} | <b>Category:</b> {last_pred.category}", styles['Normal']))
        elements.append(Paragraph(f"<b>Branch Preference:</b> {last_pred.branch_preference}", styles['Normal']))
        elements.append(Paragraph(f"<b>Timestamp:</b> {last_pred.created_at}", styles['Normal']))
        elements.append(Paragraph("<br/><br/>", styles['Normal']))
        
        # Prepare table data
        header = ["Rank", "College Name", "Branch", "Cutoff", "Prob %", "Class"]
        data = [header]
        for r in results: 
            data.append([
                r.get('rank', '-'),
                r['college_name'][:45], 
                r['branch'][:25], 
                r['cutoff'], 
                r['probability_percent'], 
                r['classification']
            ])
        
        # Split data if too large for one table, or use a smaller font
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#00ff41")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey)
        ]))
        elements.append(t)
        doc.build(elements)
    else:
        raise HTTPException(status_code=400, detail="Unsupported format")

    return FileResponse(file_path, filename=filename)
