"""
DigiPath — Refactored Backend Routing (backend_routes.py)
========================================================
Clean, modular routing for User and Admin domains.
Enforces silent data processing and End-User Transparency.
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, HTTPException, Query, Depends

# Shared in-memory collections (thread-safe by GIL in FastAPI sync routes)
FEEDBACK_STORE: List[Dict[str, Any]] = [
    {"id": "fb-001", "timestamp": "2026-06-06T10:00:00Z", "message": "Predictions are highly accurate.", "rating": 5},
    {"id": "fb-002", "timestamp": "2026-06-06T10:15:00Z", "message": "Missing hostel info for MIT Pune.", "rating": 3}
]
SECURITY_ALERTS_STORE: List[Dict[str, Any]] = [
    {"id": "sec-001", "type": "Scam", "severity": "CRITICAL", "details": "Fake offer letter detected for VJTI.", "status": "OPEN"}
]

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 1 — INSTITUTE SEARCH ENGINE (USER DOMAIN)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/institute/search")
async def search_institute(
    q:      Optional[str] = Query(None),
    city:   Optional[str] = Query(None),
    status: Optional[str] = Query(None)
):
    from app import INSTITUTE_DB  # Lazy import to avoid circular dependency
    
    q_clean  = (q or "").strip().lower()
    city_l   = (city or "").strip().lower()
    status_l = (status or "").strip().lower()

    # Fast DTE lookup
    if q_clean.isdigit() and len(q_clean) == 4:
        record = INSTITUTE_DB.get(q_clean)
        if record:
            return {"match_type": "exact", "detail": record, "total_matches": 1}

    hits = []
    for dte, record in INSTITUTE_DB.items():
        # Filtering logic
        loc = (record.get("location") or "").lower()
        st  = ((record.get("system_overview") or {}).get("status") or "").lower()
        
        if city_l and city_l not in loc: continue
        if status_l and status_l not in st: continue
        
        # Substring matching
        if q_clean and (q_clean not in record.get("name", "").lower() and q_clean not in loc):
            continue
            
        hits.append({
            "dte_code": dte,
            "name": record.get("name"),
            "location": record.get("location"),
            "system_overview": record.get("system_overview"),
            "placement_matrix": record.get("placement_matrix"),
            "academic_protocols": record.get("academic_protocols"),
            "administration_logistics": record.get("administration_logistics"),
            "predicted_2026": record.get("ai_cutoff_prediction", {}).get("predicted_2026")
        })
        if len(hits) >= 5: break

    if not hits:
        return {"match_type": "none", "total_matches": 0}
    
    if len(hits) == 1:
        return {"match_type": "exact", "detail": hits[0], "total_matches": 1}

    return {
        "match_type": "suggestions",
        "suggestions": hits,
        "total_matches": len(hits)
    }

@router.get("/institute/filters")
async def get_filters():
    from app import INSTITUTE_DB
    cities = sorted({(r.get("location") or "").split(",")[0].strip() for r in INSTITUTE_DB.values() if r.get("location")})
    statuses = sorted({(r.get("system_overview") or {}).get("status", "") for r in INSTITUTE_DB.values()} - {""})
    return {"cities": cities, "statuses": statuses}

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 2 — ADMIN TELEMETRY & HEALTH
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/feedback")
async def get_feedback():
    return {"items": FEEDBACK_STORE, "total": len(FEEDBACK_STORE)}

@router.get("/admin/reports")
async def get_reports():
    return {"items": SECURITY_ALERTS_STORE, "open_critical": sum(1 for r in SECURITY_ALERTS_STORE if r["severity"] == "CRITICAL")}

@router.get("/admin/system-health")
async def get_system_health():
    return {
        "status": "HEALTHY",
        "metrics": {
            "nodes": 414,
            "edges": 401,
            "communities": 23,
            "backend_coverage": 72.0,
            "api_sync": 61.0,
            "community_cohesion": 45.0,
            "node_connectivity": 12.0
        },
        "graphify_state": "1949c336",
        "timestamp": datetime.now(tz=timezone.utc).isoformat()
    }

@router.post("/admin/feedback")
async def post_feedback(msg: str, rating: int, target: Optional[str] = None):
    entry = {
        "id": f"fb-{int(time.time())}",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "message": msg,
        "rating": rating,
        "target_college": target
    }
    FEEDBACK_STORE.insert(0, entry)
    return {"status": "recorded"}
