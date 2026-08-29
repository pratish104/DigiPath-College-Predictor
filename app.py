"""DigiPath FastAPI application assembly and protected page routes."""

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auth_routes import get_current_admin_user, router as auth_router
from backend_routes import router as api_router
from chat_routes import router as chat_router
from database import create_database_tables
from predictor_routes import router as predictor_router
from resume_routes import router as resume_router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
INSTITUTES_JSON_PATH = os.path.join(BASE_DIR, "institutes.json")

INSTITUTE_DB: dict[str, dict[str, Any]] = {}

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("digipath")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def refine_data(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize institute JSON values before exposing them to search endpoints."""
    refined: dict[str, dict[str, Any]] = {}
    for dte_code, source in raw.items():
        if not isinstance(source, dict):
            continue
        data = dict(source)
        location = str(data.get("location") or "Unknown, Maharashtra").strip()
        data["location"] = location.replace("NaviMumbai", "Navi Mumbai").title()

        system_overview = data.get("system_overview")
        if not isinstance(system_overview, dict):
            system_overview = {}
        system_overview.setdefault("status", "Un-Aided")
        data["system_overview"] = system_overview

        cutoff_prediction = data.get("ai_cutoff_prediction")
        if not isinstance(cutoff_prediction, dict):
            cutoff_prediction = {}
        if not cutoff_prediction.get("predicted_2026"):
            try:
                prior_cutoff = float(cutoff_prediction.get("actual_2025", 0))
                cutoff_prediction["predicted_2026"] = str(round(prior_cutoff + 0.45, 2)) if prior_cutoff > 0 else "N/A"
            except (TypeError, ValueError):
                cutoff_prediction["predicted_2026"] = "N/A"
        data["ai_cutoff_prediction"] = cutoff_prediction
        refined[str(dte_code).strip()] = data
    return refined


def load_institute_data() -> None:
    """Load institute data once at startup and fail safely if it is invalid."""
    global INSTITUTE_DB
    if not os.path.isfile(INSTITUTES_JSON_PATH):
        raise RuntimeError(f"Required institute data file is missing: {INSTITUTES_JSON_PATH}")
    try:
        with open(INSTITUTES_JSON_PATH, "r", encoding="utf-8") as file_handle:
            raw_data = json.load(file_handle)
        if not isinstance(raw_data, dict):
            raise ValueError("institutes.json must contain an object keyed by DTE code")
        INSTITUTE_DB = refine_data(raw_data)
        log.info("Loaded %d institute records.", len(INSTITUTE_DB))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.exception("Institute data initialization failed.")
        raise RuntimeError("Unable to load institute data.") from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Starting DigiPath.")
    create_database_tables()
    load_institute_data()
    yield
    log.info("Stopping DigiPath.")


app = FastAPI(title="DigiPath", version="3.1.0", lifespan=lifespan)

allowed_origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
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
@app.get("/admin_dashboard")
async def admin_dashboard(request: Request, _: Any = Depends(get_current_admin_user)):
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
@app.get("/admin_panel")
async def admin_panel(request: Request, _: Any = Depends(get_current_admin_user)):
    return templates.TemplateResponse(request=request, name="admin_dashboard.html")
