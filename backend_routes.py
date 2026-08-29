"""Public institute search and administrator-only operational API routes."""

import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from auth_routes import get_current_admin_user
from models import User

FEEDBACK_STORE: list[dict[str, Any]] = [
    {"id": "fb-001", "timestamp": "2026-06-06T10:00:00Z", "message": "Predictions are highly accurate.", "rating": 5},
    {"id": "fb-002", "timestamp": "2026-06-06T10:15:00Z", "message": "Missing hostel info for MIT Pune.", "rating": 3},
]
SECURITY_ALERTS_STORE: list[dict[str, Any]] = [
    {"id": "sec-001", "type": "Scam", "severity": "CRITICAL", "details": "Fake offer letter detected for VJTI.", "status": "OPEN"},
]

router = APIRouter()


class FeedbackCreate(BaseModel):
    msg: str = Field(min_length=1, max_length=2000)
    rating: int = Field(ge=1, le=5)
    target: Optional[str] = Field(default=None, max_length=255)


@router.get("/institute/search")
async def search_institute(
    q: Optional[str] = Query(default=None, max_length=200),
    city: Optional[str] = Query(default=None, max_length=100),
    status: Optional[str] = Query(default=None, max_length=100),
) -> dict[str, Any]:
    from app import INSTITUTE_DB

    q_clean = (q or "").strip().lower()
    city_clean = (city or "").strip().lower()
    status_clean = (status or "").strip().lower()
    if q_clean.isdigit() and len(q_clean) == 4:
        record = INSTITUTE_DB.get(q_clean)
        if record:
            detail = dict(record)
            detail.setdefault("dte_code", q_clean)
            return {"match_type": "exact", "detail": detail, "total_matches": 1}

    hits: list[dict[str, Any]] = []
    for dte_code, record in INSTITUTE_DB.items():
        if not isinstance(record, dict):
            continue
        location = str(record.get("location") or "")
        system_overview = record.get("system_overview") or {}
        record_status = str(system_overview.get("status") or "") if isinstance(system_overview, dict) else ""
        college_name = str(record.get("name") or "")
        if city_clean and city_clean not in location.lower():
            continue
        if status_clean and status_clean not in record_status.lower():
            continue
        if q_clean and q_clean not in college_name.lower() and q_clean not in location.lower():
            continue
        hits.append({
            "dte_code": dte_code, "name": record.get("name"), "location": record.get("location"),
            "system_overview": record.get("system_overview"), "placement_matrix": record.get("placement_matrix"),
            "academic_protocols": record.get("academic_protocols"),
            "administration_logistics": record.get("administration_logistics"),
            "predicted_2026": (record.get("ai_cutoff_prediction") or {}).get("predicted_2026"),
        })
        if len(hits) == 5:
            break
    if not hits:
        return {"match_type": "none", "total_matches": 0}
    if len(hits) == 1:
        return {"match_type": "exact", "detail": hits[0], "total_matches": 1}
    return {"match_type": "suggestions", "suggestions": hits, "total_matches": len(hits)}


@router.get("/institute/filters")
async def get_filters() -> dict[str, list[str]]:
    from app import INSTITUTE_DB

    cities = sorted({str(record.get("location") or "").split(",")[0].strip() for record in INSTITUTE_DB.values() if isinstance(record, dict) and record.get("location")} - {""})
    statuses = sorted({str((record.get("system_overview") or {}).get("status") or "").strip() for record in INSTITUTE_DB.values() if isinstance(record, dict) and isinstance(record.get("system_overview"), dict)} - {""})
    return {"cities": cities, "statuses": statuses}


@router.get("/admin/feedback")
async def get_feedback(_: User = Depends(get_current_admin_user)) -> dict[str, Any]:
    return {"items": FEEDBACK_STORE, "total": len(FEEDBACK_STORE)}


@router.get("/admin/reports")
async def get_reports(_: User = Depends(get_current_admin_user)) -> dict[str, Any]:
    return {"items": SECURITY_ALERTS_STORE, "open_critical": sum(1 for report in SECURITY_ALERTS_STORE if report["severity"] == "CRITICAL")}


@router.get("/admin/system-health")
async def get_system_health(_: User = Depends(get_current_admin_user)) -> dict[str, Any]:
    return {"status": "HEALTHY", "metrics": {"nodes": 414, "edges": 401, "communities": 23, "backend_coverage": 72.0, "api_sync": 61.0, "community_cohesion": 45.0, "node_connectivity": 12.0}, "graphify_state": "1949c336", "timestamp": datetime.now(tz=timezone.utc).isoformat()}


@router.post("/admin/feedback")
async def post_feedback(payload: FeedbackCreate, _: User = Depends(get_current_admin_user)) -> dict[str, str]:
    entry = {"id": f"fb-{int(time.time() * 1000)}", "timestamp": datetime.now(tz=timezone.utc).isoformat(), "message": payload.msg.strip(), "rating": payload.rating, "target_college": payload.target.strip() if payload.target else None}
    FEEDBACK_STORE.insert(0, entry)
    return {"status": "recorded"}
