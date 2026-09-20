"""DigiPath FastAPI application assembly, route protection, and web pages.

Environment is hydrated here so that any top-level os.getenv() call
inside this module resolves correctly across all environments.
"""

# ── 1. Environment hydration ─────────────────────────────────────────────────
import pathlib

from dotenv import load_dotenv

_ENV_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)
# ─────────────────────────────────────────────────────────────────────────────

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.util import get_remote_address
    SLOWAPI_AVAILABLE = True
except ImportError:
    SLOWAPI_AVAILABLE = False
    class Limiter:
        def __init__(self, *args, **kwargs): pass
        def limit(self, *args, **kwargs):
            return lambda func: func
        async def _check_request_limit(self, *args, **kwargs): pass
    def _rate_limit_exceeded_handler(*args, **kwargs): pass
    class RateLimitExceeded(Exception): pass
    class SlowAPIMiddleware:
        def __init__(self, app): self.app = app
        async def __call__(self, scope, receive, send): await self.app(scope, receive, send)
    def get_remote_address(*args, **kwargs): return "127.0.0.1"

from sqlalchemy.orm import Session

import auth_service
import models
from auth_routes import _extract_bearer_token, get_current_admin_user, router as auth_router
from backend_routes import router as api_router
from chat_routes import router as chat_router
from database import create_database_tables, get_db
from predictor_routes import router as predictor_router
from resume_routes import router as resume_router
from cashfree_routes import router as cashfree_router
from admin_routes import router as admin_router
from user_service import UserService

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR: str = os.path.join(BASE_DIR, "templates")
STATIC_DIR: str = os.path.join(BASE_DIR, "static")
DATA_DIR: str = os.path.join(BASE_DIR, "data")
INSTITUTES_JSON_PATH: str = os.path.join(DATA_DIR, "institutes.json")
if not os.path.isfile(INSTITUTES_JSON_PATH):
    INSTITUTES_JSON_PATH = os.path.join(BASE_DIR, "institutes.json")

# In-memory institute registry populated once at startup.
INSTITUTE_DB: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("digipath")

# ---------------------------------------------------------------------------
# Global Rate Limiter (shared singleton — imported by routers)
# ---------------------------------------------------------------------------

limiter: Limiter = Limiter(key_func=get_remote_address, default_limits=[])

# ---------------------------------------------------------------------------
# Jinja2 template renderer
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ---------------------------------------------------------------------------
# Auth Helper for Page Route Protection
# ---------------------------------------------------------------------------

def _get_page_user(request: Request, db: Session) -> Optional[models.User]:
    """Resolve active authenticated user from Authorization header or HTTP cookie."""
    token = _extract_bearer_token(request.headers.get("Authorization"), request)
    if not token:
        return None
    payload = auth_service.get_token_payload(token)
    if not payload or not payload.get("sub"):
        return None
    email = str(payload.get("sub")).strip().lower()
    try:
        return db.query(models.User).filter(models.User.email == email).first()
    except Exception as exc:
        log.warning("Page auth user resolution error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Institute data helpers
# ---------------------------------------------------------------------------

def refine_data(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize institute JSON values before exposing them to search endpoints."""
    refined: dict[str, dict[str, Any]] = {}
    for dte_code, source in raw.items():
        if not isinstance(source, dict):
            continue
        data = dict(source)
        zcode = str(dte_code).strip().zfill(5)
        data["dte_code"] = zcode

        city = data.get("city") or ""
        location = str(data.get("location") or city or "Unknown, Maharashtra").strip()
        data["location"] = location.replace("NaviMumbai", "Navi Mumbai").title()
        if not data.get("city") and location:
            data["city"] = location.split(",")[0].strip()

        system_overview = data.get("system_overview")
        if not isinstance(system_overview, dict):
            system_overview = {}
        system_overview.setdefault("status", data.get("status") or "Un-Aided")
        system_overview.setdefault("autonomy", data.get("autonomy") or "Non-Autonomous")
        data["system_overview"] = system_overview

        cutoff_prediction = data.get("ai_cutoff_prediction")
        if not isinstance(cutoff_prediction, dict):
            cutoff_prediction = {}
        if not cutoff_prediction.get("predicted_2026"):
            try:
                prior_cutoff = float(data.get("actual_2025") or cutoff_prediction.get("actual_2025", 0))
                cutoff_prediction["predicted_2026"] = (
                    round(prior_cutoff + 0.45, 2) if prior_cutoff > 0 else "N/A"
                )
            except (TypeError, ValueError):
                cutoff_prediction["predicted_2026"] = "N/A"
        data["ai_cutoff_prediction"] = cutoff_prediction
        data["predicted_2026"] = data.get("predicted_2026") or cutoff_prediction.get("predicted_2026")

        refined[zcode] = data
    return refined


def load_institute_data() -> None:
    """Load institute data once at startup; raise with a clear message if invalid."""
    global INSTITUTE_DB
    target_path = INSTITUTES_JSON_PATH
    if not os.path.isfile(target_path):
        target_path = os.path.join(BASE_DIR, "institutes.json")
    if not os.path.isfile(target_path):
        raise RuntimeError(
            f"[STARTUP] Required institute data file is missing: {INSTITUTES_JSON_PATH}"
        )
    try:
        with open(target_path, "r", encoding="utf-8") as fh:
            raw_data = json.load(fh)
        if not isinstance(raw_data, dict):
            raise ValueError("institutes.json must contain an object keyed by DTE code.")
        INSTITUTE_DB = refine_data(raw_data)
        log.info("[STARTUP] Loaded %d institute records from %s.", len(INSTITUTE_DB), target_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.exception("[STARTUP] Institute data initialization failed.")
        raise RuntimeError("Unable to load institute data.") from exc


# ---------------------------------------------------------------------------
# Application lifespan (startup / shutdown hooks)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Run startup tasks before serving requests; log on shutdown."""
    log.info("=" * 60)
    log.info("DigiPath Cyberpunk Neural Core is starting up...")
    log.info("  .env loaded from : %s", _ENV_PATH)
    log.info("  LOG_LEVEL        : %s", os.getenv('LOG_LEVEL', 'INFO'))
    log.info("  ALLOWED_ORIGINS  : %s", os.getenv('ALLOWED_ORIGINS', 'http://localhost:8000'))
    log.info("=" * 60)

    create_database_tables()
    load_institute_data()

    # Seed default administrator account if missing
    db_gen = get_db()
    db = next(db_gen)
    try:
        UserService.seed_admin_user(db)
    finally:
        db.close()

    log.info("DigiPath startup complete — ready to accept requests.")
    yield
    log.info("DigiPath is shutting down. Goodbye.")


# ---------------------------------------------------------------------------
# FastAPI application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="DigiPath",
    version="4.4.0",
    description=(
        "Educational technology platform for CET/Diploma college predictions, "
        "job recommendations, DigiPath 5D ATS resume intelligence, professional resume builder, "
        "fraud threat intelligence, and RLHF AI assistance."
    ),
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Slowapi rate-limiting middleware
# ---------------------------------------------------------------------------

app.state.limiter = limiter
if SLOWAPI_AVAILABLE:
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

_raw_origins: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
allowed_origins: list[str] = [
    origin.strip() for origin in _raw_origins.split(",") if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---------------------------------------------------------------------------
# Static files & API routers
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(api_router,       prefix="/api")
app.include_router(auth_router,      prefix="/api")
app.include_router(predictor_router, prefix="/api")
app.include_router(resume_router,    prefix="/api")
app.include_router(chat_router,      prefix="/api")
app.include_router(cashfree_router,  prefix="/api")
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Public Page routes
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Cheap process-health endpoint for the hosting platform."""
    return {"status": "ok"}

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="login.html", context={"tab": "register"}
    )


@app.get("/institutes")
async def institutes_page(request: Request):
    return templates.TemplateResponse(request=request, name="institutes.html")


@app.get("/scam-detector")
async def scam_detector_page(request: Request):
    return templates.TemplateResponse(request=request, name="scam_detector.html")


@app.get("/chatbot")
async def chatbot_page(request: Request):
    return templates.TemplateResponse(request=request, name="chatbot.html")


@app.get("/company-verification")
async def company_verification_page(request: Request):
    return templates.TemplateResponse(request=request, name="company_verification.html")


@app.get("/report-scam")
async def report_scam_page(request: Request):
    return templates.TemplateResponse(request=request, name="report_scam.html")


# ---------------------------------------------------------------------------
# Protected Page routes (Mandatory Authentication Guards)
# ---------------------------------------------------------------------------

@app.get("/dashboard")
async def user_dashboard(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    if not user:
        return RedirectResponse(url="/login?next=/dashboard", status_code=302)
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"user": user})


@app.get("/profile")
async def user_profile_page(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    if not user:
        return RedirectResponse(url="/login?next=/profile", status_code=302)
    return templates.TemplateResponse(request=request, name="profile.html", context={"user": user})


@app.get("/resume-analyzer")
async def resume_analyzer_page(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    if not user:
        return RedirectResponse(url="/login?next=/resume-analyzer", status_code=302)
    return templates.TemplateResponse(request=request, name="resume_analyzer.html", context={"user": user})


@app.get("/resume-builder")
async def resume_builder_page(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    if not user:
        return RedirectResponse(url="/login?next=/resume-builder", status_code=302)
    return templates.TemplateResponse(request=request, name="resume_builder.html", context={"user": user})


@app.get("/predictor")
async def predictor_page(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    if not user:
        return RedirectResponse(url="/login?next=/predictor", status_code=302)
    return templates.TemplateResponse(request=request, name="predictor.html", context={"user": user})


@app.get("/job-recommender")
async def job_recommender_page(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    if not user:
        return RedirectResponse(url="/login?next=/job-recommender", status_code=302)
    return templates.TemplateResponse(request=request, name="job_recommender.html", context={"user": user})


@app.get("/roadmap")
async def roadmap_page(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    if not user:
        return RedirectResponse(url="/login?next=/roadmap", status_code=302)
    return templates.TemplateResponse(request=request, name="roadmap.html", context={"user": user})


@app.get("/pricing")
async def pricing_page(request: Request, db: Session = Depends(get_db)):
    user = _get_page_user(request, db)
    return templates.TemplateResponse(request=request, name="pricing.html", context={"user": user})


# ---------------------------------------------------------------------------
# Strict Admin Moderation Route Guard
# ---------------------------------------------------------------------------

@app.get("/admin")
@app.get("/admin-dashboard")
@app.get("/admin_dashboard")
@app.get("/admin_panel")
async def admin_dashboard_page(request: Request, db: Session = Depends(get_db)):
    """Strict Administrator Role Guard: Restricts access to Support Admins and Super Admins."""
    user = _get_page_user(request, db)

    # 1. Unauthenticated -> Redirect to Login with next parameter
    if not user:
        return RedirectResponse(url="/login?next=/admin-dashboard", status_code=302)

    admin_lvl = int(user.admin_level or 0)
    user_role = (user.role or "").upper()
    if user_role in ("SUPER_ADMIN", "ADMIN"):
        admin_lvl = max(admin_lvl, 2)
    elif user_role == "SUPPORT_ADMIN":
        admin_lvl = max(admin_lvl, 1)

    # 2. Authenticated but lacks admin clearance -> 403 screen
    if admin_lvl < 1:
        log.warning("Access denied to /admin for user: %s (Role: %s, Level: %d)", user.email, user.role, admin_lvl)
        return HTMLResponse(
            status_code=403,
            content="""
            <!DOCTYPE html>
            <html lang="en">
            <head>
            <meta charset="UTF-8">
            <title>DIGIPATH // ACCESS_DENIED_403</title>
            <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@900&display=swap" rel="stylesheet">
            <style>
              body { background: #030712; color: #ef4444; font-family: 'Share Tech Mono', monospace; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; margin:0; text-align:center; padding:20px; }
              h1 { font-family: 'Orbitron', sans-serif; font-size: 32px; letter-spacing: 3px; margin-bottom: 8px; text-shadow: 0 0 20px rgba(239,68,68,0.5); }
              p { color: #f87171; font-size: 14px; max-width: 520px; line-height: 1.6; margin-bottom: 24px; }
              a { border: 1px solid #ef4444; color: #ef4444; padding: 10px 20px; text-decoration: none; font-size: 12px; letter-spacing: 1.5px; transition: all 0.2s; }
              a:hover { background: #ef4444; color: #fff; box-shadow: 0 0 15px #ef4444; }
            </style>
            </head>
            <body>
              <h1>[ 403_FORBIDDEN: ADMIN_CLEARANCE_REQUIRED ]</h1>
              <p>&gt; SECURITY_VIOLATION: Elevated Administrator privileges required to enter the Root Command Center. Your account lacks minimum Level-1 clearance.</p>
              <a href="/dashboard">&lt; RETURN_TO_CANDIDATE_DASHBOARD</a>
            </body>
            </html>
            """
        )

    # 3. Compute Metrics for admin_dashboard.html
    from sqlalchemy import func
    total_rev_res = db.query(func.sum(models.Transaction.amount)).filter(models.Transaction.payment_status == "PAID").scalar()
    total_revenue = float(total_rev_res or 0.0)
    total_users = db.query(func.count(models.User.id)).scalar() or 0
    premium_users_count = db.query(func.count(models.User.id)).filter(models.User.credits_balance > 50).scalar() or 0
    unresolved_errors_count = db.query(func.count(models.SystemErrorLog.id)).filter(models.SystemErrorLog.status == "UNRESOLVED").scalar() or 0
    total_transactions_count = db.query(func.count(models.Transaction.id)).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "user": user,
            "admin_level": admin_lvl,
            "total_revenue": total_revenue,
            "total_users": total_users,
            "premium_users_count": premium_users_count,
            "unresolved_errors_count": unresolved_errors_count,
            "total_transactions_count": total_transactions_count,
        },
    )
