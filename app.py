"""
DigiPath — Core Production Server (app.py)
==========================================
Principal Engineer Refactor: Centralized logic, silent data pipeline.
"""

import os
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Internal imports
from backend_routes import router as api_router
from auth_routes import router as auth_router
from predictor_routes import router as predictor_router
from resume_routes import router as resume_router
from chat_routes import router as chat_router
import models
from database import engine
from migrate_db import run_migration

# ─────────────────────────────────────────────────────────────────────────────
# GLOBALS & CONFIG
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

INSTITUTE_DB: Dict[str, Any] = {}
INSTITUTES_JSON_PATH = os.path.join(BASE_DIR, "institutes.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("digipath")

log.info("📂 TEMPLATES_DIR: %s", TEMPLATES_DIR)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ─────────────────────────────────────────────────────────────────────────────
# SERVER-SIDE DATA REFINEMENT (Silent Pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def refine_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Consolidates clean_city_names, clean_missing_values, etc. into one pass."""
    refined = {}
    for dte, data in raw.items():
        if not isinstance(data, dict): continue
        
        # 1. Normalize City/Location
        loc = data.get("location", "Unknown, Maharashtra")
        if "Navi" in loc and "Mumbai" in loc: loc = loc.replace("NaviMumbai", "Navi Mumbai")
        data["location"] = loc.strip().title()
        
        # 2. Fix Missing Values / Empty Cutoffs
        sys_ov = data.get("system_overview", {})
        if not sys_ov.get("status"): sys_ov["status"] = "Un-Aided"
        data["system_overview"] = sys_ov
        
        # 3. Predicted 2026 logic (ensure it exists for UI emerald gauge)
        acp = data.get("ai_cutoff_prediction", {})
        if not acp.get("predicted_2026"):
            # Fallback heuristic if missing
            actual_25 = acp.get("actual_2025", "0")
            try:
                base = float(actual_25)
                acp["predicted_2026"] = str(round(base + 0.45, 2)) if base > 0 else "N/A"
            except:
                acp["predicted_2026"] = "N/A"
        data["ai_cutoff_prediction"] = acp
        
        refined[str(dte).strip()] = data
    return refined

# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE HOOKS
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global INSTITUTE_DB
    log.info("🚀 Booting DigiPath Pipeline...")
    
    # Initialize Database
    try:
        run_migration()
        models.Base.metadata.create_all(bind=engine)
        log.info("✅ Database Tables Initialized.")
    except Exception as e:
        log.error("❌ Database Initialization Failed: %s", e)

    # Preload and Refine
    try:
        if os.path.exists(INSTITUTES_JSON_PATH):
            with open(INSTITUTES_JSON_PATH, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            INSTITUTE_DB = refine_data(raw_data)
            log.info("✅ Pipeline Success: %d records refined and cached.", len(INSTITUTE_DB))
        else:
            log.error("❌ Critical: %s missing.", INSTITUTES_JSON_PATH)
    except Exception as e:
        log.exception("❌ Pipeline Failure: %s", e)

    yield
    log.info("🛑 DigiPath shutting down.")

# ─────────────────────────────────────────────────────────────────────────────
# APP ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="DigiPath", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static and Routes
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(api_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(predictor_router, prefix="/api")
app.include_router(resume_router, prefix="/api")
app.include_router(chat_router, prefix="/api")

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"tab": "register"})

@app.get("/dashboard")
async def user_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")

@app.get("/admin-dashboard")
async def admin_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="admin_dashboard.html")

@app.get("/predictor")
async def predictor_page(request: Request):
    return templates.TemplateResponse(request=request, name="predictor.html")

@app.get("/institutes")
async def institutes_page(request: Request):
    return templates.TemplateResponse(request=request, name="institutes.html")

@app.get("/scam-detector")
async def scam_detector_page(request: Request):
    return templates.TemplateResponse(request=request, name="scam_detector.html")

@app.get("/roadmap")
async def roadmap_page(request: Request):
    return templates.TemplateResponse(request=request, name="roadmap.html")

@app.get("/resume-analyzer")
async def resume_analyzer_page(request: Request):
    return templates.TemplateResponse(request=request, name="resume_analyzer.html")

@app.get("/chatbot")
async def chatbot_page(request: Request):
    return templates.TemplateResponse(request=request, name="chatbot.html")

@app.get("/job-recommender")
async def job_recommender_page(request: Request):
    return templates.TemplateResponse(request=request, name="job_recommender.html")

@app.get("/company-verification")
async def company_verification_page(request: Request):
    return templates.TemplateResponse(request=request, name="company_verification.html")

@app.get("/report-scam")
async def report_scam_page(request: Request):
    return templates.TemplateResponse(request=request, name="report_scam.html")

@app.get("/admin")
async def admin_panel(request: Request):
    return templates.TemplateResponse(request=request, name="admin_dashboard.html")

# uvicorn app:app --reload
