"""Deterministic, production-grade CET and Diploma / DSE prediction engine for DigiPath.

Provides:
  - Unified Category & Seat Code Query Expansion
  - Mathematical 2026 weighted forecasting: C_2026 = 0.6 * C_2025 + 0.4 * C_2024
  - Logistic Probability Formula: P = 1 / (1 + exp(-0.55 * (S - C_2026))) * 100%
  - Uncapped Safe, Target, and Dream Multi-Tier Classification
  - Automatic Regional Proximity Fallback when local matches < 15
  - Dual-pathway dataset binding for MHT-CET (FE) and Diploma (DSE)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from data_loader import (
    COL_BRANCH,
    COL_CATEGORY,
    COL_CITY,
    COL_COLLEGE_CODE,
    COL_COLLEGE_NAME,
    COL_CUTOFF,
    COL_EXAM_TYPE,
    COL_ROUND,
    COL_SEAT_CODE,
    COL_SEAT_TYPE,
    COL_TYPE,
    COL_YEAR,
    DataLoader,
    _CATEGORY_MAP,
    _clean_and_resolve_city,
)

log = logging.getLogger("digipath.cet_predictor")

# ── Regional Proximity & Adjacency Master Map ─────────────────────────────────
ADJACENT_REGIONS: Mapping[str, list[str]] = MappingProxyType(
    {
        "Navi Mumbai": ["Navi Mumbai", "Mumbai", "Thane", "Raigad"],
        "Mumbai": ["Mumbai", "Thane", "Navi Mumbai", "Palghar"],
        "Thane": ["Thane", "Mumbai", "Navi Mumbai", "Palghar"],
        "Palghar": ["Palghar", "Thane", "Mumbai"],
        "Raigad": ["Raigad", "Navi Mumbai", "Thane", "Pune"],
        "Pune": ["Pune", "Satara", "Ahmednagar", "Solapur"],
        "Nashik": ["Nashik", "Ahmednagar", "Dhule", "Jalgaon"],
        "Nagpur": ["Nagpur", "Wardha", "Bhandara", "Gondia", "Chandrapur"],
        "Chhatrapati Sambhajinagar": ["Chhatrapati Sambhajinagar", "Jalna", "Beed", "Ahmednagar"],
        "Kolhapur": ["Kolhapur", "Sangli", "Satara"],
        "Sangli": ["Sangli", "Kolhapur", "Satara", "Solapur"],
        "Satara": ["Satara", "Pune", "Sangli", "Kolhapur"],
        "Solapur": ["Solapur", "Pune", "Sangli", "Dharashiv", "Osmanabad"],
        "Amravati": ["Amravati", "Akola", "Wardha", "Nagpur", "Yavatmal"],
        "Akola": ["Akola", "Amravati", "Washim", "Buldhana"],
        "Buldhana": ["Buldhana", "Akola", "Jalgaon"],
        "Jalgaon": ["Jalgaon", "Dhule", "Nashik", "Buldhana"],
        "Dhule": ["Dhule", "Jalgaon", "Nashik"],
        "Nanded": ["Nanded", "Latur", "Parbhani", "Hingoli", "Yavatmal"],
        "Latur": ["Latur", "Nanded", "Dharashiv", "Osmanabad", "Beed"],
        "Dharashiv": ["Dharashiv", "Osmanabad", "Latur", "Solapur", "Beed"],
        "Osmanabad": ["Dharashiv", "Osmanabad", "Latur", "Solapur", "Beed"],
        "Beed": ["Beed", "Chhatrapati Sambhajinagar", "Jalna", "Latur", "Dharashiv", "Osmanabad"],
        "Jalna": ["Jalna", "Chhatrapati Sambhajinagar", "Parbhani", "Beed"],
        "Parbhani": ["Parbhani", "Jalna", "Nanded", "Hingoli"],
        "Hingoli": ["Hingoli", "Nanded", "Parbhani", "Washim"],
        "Washim": ["Washim", "Akola", "Amravati", "Hingoli", "Yavatmal"],
        "Yavatmal": ["Yavatmal", "Amravati", "Wardha", "Chandrapur", "Nanded"],
        "Wardha": ["Wardha", "Nagpur", "Amravati", "Yavatmal", "Chandrapur"],
        "Chandrapur": ["Chandrapur", "Nagpur", "Wardha", "Yavatmal", "Gadchiroli"],
        "Bhandara": ["Bhandara", "Nagpur", "Gondia"],
        "Gondia": ["Gondia", "Bhandara", "Nagpur"],
        "Gadchiroli": ["Gadchiroli", "Chandrapur"],
        "Ratnagiri": ["Ratnagiri", "Sindhudurg", "Raigad", "Kolhapur"],
        "Sindhudurg": ["Sindhudurg", "Ratnagiri", "Kolhapur"],
        "Ahmednagar": ["Ahmednagar", "Pune", "Nashik", "Chhatrapati Sambhajinagar", "Solapur"],
    }
)

# ── Category & Seat Code Expansion Mapping ────────────────────────────────────
CATEGORY_MAP: dict[str, list[str]] = {
    'OPEN':  ['OPEN', 'GOPENH', 'GOPENO', 'LOPENH', 'LOPENO', 'AI', 'GOPEN', 'LOPEN'],
    'SC':    ['SC', 'GSC', 'GSCH', 'GSCO', 'LSC', 'LSCH', 'LSCO'],
    'ST':    ['ST', 'GST', 'VST', 'STH', 'STO', 'LST', 'LSTH', 'LSTO'],
    'OBC':   ['OBC', 'GOBC', 'GOBCH', 'GOBCO', 'LOBC', 'LOBCH', 'LOBCO'],
    'VJ_DT': ['VJ', 'DT', 'NT-A', 'GVJ', 'LVJ', 'GVJH', 'GVJO', 'LVJH', 'LVJO'],
    'NT1':   ['NT-1', 'NT1', 'NT-B', 'GNT1', 'LNT1', 'GNT1H', 'GNT1O', 'LNT1H', 'LNT1O'],
    'NT2':   ['NT-2', 'NT2', 'NT-C', 'GNT2', 'LNT2', 'GNT2H', 'GNT2O', 'LNT2H', 'LNT2O'],
    'NT3':   ['NT-3', 'NT3', 'NT-D', 'GNT3', 'LNT3', 'GNT3H', 'GNT3O', 'LNT3H', 'LNT3O'],
    'SEBC':  ['SEBC', 'GSEBC', 'LSEBC', 'GSEBCH', 'GSEBCO', 'LSEBCH', 'LSEBCO'],
    'EWS':   ['EWS'],
    'TFWS':  ['TFWS'],
    'DEF':   ['DEF', 'DEFOPEN', 'DEFSC', 'DEFOBC', 'DEF-1', 'DEF-2', 'DEF-3', 'DEF1', 'DEF2', 'DEF3'],
    'PWD':   ['PWD', 'PWDOPEN', 'PWDSC', 'PWDOBC', 'PWD-O', 'PWD-R', 'PH'],
    'MI':    ['MI', 'MINORITY', 'MIN'],
}

# The cutoff CSV stores *seat types*, while a student declares a reservation
# category.  These are deliberately separate concepts.  The small mapping
# below is only used to interpret the families that are actually present in
# the bundled FE/DSE data; it never alters the raw ``seat_code`` column.
#
# The CAP portal legend confirms G/L (general/ladies), H/O/S (home university /
# other than home university / state level), and DEFR/PWDR.  See the 2025 FE
# CAP allotment legend: https://fe2025.mahacet.org/CAP-I/CAPR-I_05370.pdf
_STUDENT_CATEGORY_TO_FAMILY: Mapping[str, str] = MappingProxyType({
    "OPEN": "OPEN", "OBC": "OBC", "SC": "SC", "ST": "ST",
    "VJ-A": "VJ", "VJ-DT": "VJ", "VJ/DT": "VJ", "VJ/DT-NT(A)": "VJ", "VJ": "VJ", "NT": "NT",
    "NT-A": "NT-A", "NT-B": "NT-B", "NT-C": "NT-C", "NT-D": "NT-D",
    "SBC": "OBC", "SEBC": "SEBC", "EWS": "EWS",
})


def _seat_base_family(code: str) -> Optional[str]:
    """Return the reservation family encoded by one raw CSV seat code.

    Special-seat prefixes are removed first, so e.g. ``DEFRSC`` is an SC
    defence record and ``PWDOBC`` is an OBC disability record.  Unknown CSV
    values remain unknown instead of being guessed into a category.
    """
    value = str(code or "").strip().upper()
    if value.startswith(("PWD", "DEF")):
        value = re.sub(r"^(?:PWD|DEF)(?:R|-R)?", "", value)
    value = value.lstrip("-")
    if value.startswith(("GOPEN", "LOPEN", "OPEN")) or value in {"O", "OPE"}:
        return "OPEN"
    if "OBC" in value or value.endswith("ROB"):
        return "OBC"
    if "SC" in value:
        return "SC"
    if "ST" in value:
        return "ST"
    if "SEB" in value or "SEBC" in value:
        return "SEBC"
    if "VJ" in value:
        return "VJ"
    nt_match = re.search(r"NT(?:-|)?([ABCD123])", value)
    if nt_match:
        # CAP's numeric NT1/NT2/NT3 labels correspond to NT-B/NT-C/NT-D;
        # the DSE CSV spells the same groups directly as NTA/NTB/NTC/NTD.
        subtype = {"1": "B", "2": "C", "3": "D"}.get(nt_match.group(1), nt_match.group(1))
        return f"NT-{subtype}"
    if "NT" in value:
        return "NT"
    if "EWS" in value:
        return "EWS"
    return None


def _seat_is_ladies(code: str) -> bool:
    """Ladies CAP records use the L prefix in the source CSV."""
    return str(code or "").strip().upper().startswith("L")


def _seat_special_eligibility(code: str) -> Optional[str]:
    value = str(code or "").strip().upper()
    if value.startswith("DEF"):
        return "DEFENCE"
    if value.startswith("PWD"):
        return "PWD"
    if value in {"ORPHAN", "ORP"}:
        return "ORPHAN"
    if value == "TFWS":
        return "TFWS"
    if value in {"MI", "MINORITY", "MIN"}:
        return "MINORITY"
    return None


def _seat_university_scope(code: str) -> str:
    """Read only the documented H/O/S suffix; absent suffix is unrestricted."""
    value = str(code or "").strip().upper()
    if value.endswith("H"):
        return "HOME"
    if value.endswith("O"):
        return "OTHER"
    if value.endswith("S"):
        return "STATE"
    return "ANY"


def _seat_applicability_reason(code: str, candidate_category: Optional[str]) -> str:
    """Provide a student-facing reason without exposing implementation fields."""
    family, selected_special = _parse_student_category(candidate_category)
    special = _seat_special_eligibility(code)
    family_label = {"OPEN": "Open", "VJ": "VJ/DT"}.get(family, family)
    if special == "PWD":
        return f"PwD seat for {family_label} category"
    if special == "DEFENCE":
        return f"Defence-reservation seat for {family_label} category"
    if special == "TFWS":
        return "Tuition Fee Waiver Scheme opportunity"
    if special == "ORPHAN":
        return "Orphan-reservation opportunity"
    if special == "MINORITY":
        return "Minority-seat opportunity"
    if _seat_base_family(code) == "OPEN":
        return "Open-seat opportunity based on merit"
    suffix = " ladies seat" if _seat_is_ladies(code) else " category seat"
    return f"{family_label}{suffix}"


def _parse_student_category(selection: Optional[str]) -> tuple[str, set[str]]:
    """Decode one student-facing category choice, never a raw CAP code.

    Special eligibility choices use an internal ``SPECIAL|FAMILY`` value so a
    student makes exactly one plain-language selection while the engine still
    knows both the special eligibility and underlying reservation family.
    """
    value = str(selection or "OPEN").strip().upper()
    special, delimiter, family = value.partition("|")
    if delimiter and special in {"LADIES", "DEFENCE", "PWD", "ORPHAN", "TFWS", "MINORITY"}:
        return _STUDENT_CATEGORY_TO_FAMILY.get(family, family or "OPEN"), {special}
    return _STUDENT_CATEGORY_TO_FAMILY.get(value, str(_CATEGORY_MAP.get(value, value)).upper()), set()


def student_category_options(data: pd.DataFrame) -> list[dict[str, str]]:
    """Build the student category selector from the raw seat codes in a dataset.

    The function intentionally publishes only verified student concepts, never
    raw labels such as GOPENH or DEFRSC.  A concept appears only if matching
    source records exist for the selected FE/DSE dataset.
    """
    raw_codes = {str(code).strip().upper() for code in data.get(COL_SEAT_CODE, pd.Series(dtype="string")).dropna()}
    base_families = {_seat_base_family(code) for code in raw_codes}
    options: list[dict[str, str]] = []
    normal = [
        ("OPEN", "Open"), ("OBC", "OBC"), ("SBC", "SBC (uses OBC CAP records)"), ("SC", "SC"), ("ST", "ST"),
        ("VJ-DT", "VJ/DT"), ("SEBC", "SEBC"), ("EWS", "EWS"),
    ]
    for value, label in normal:
        family = _STUDENT_CATEGORY_TO_FAMILY[value]
        if family in base_families:
            options.append({"value": value, "label": label, "group": "Reservation category"})
        if any(_seat_base_family(code) == family and _seat_is_ladies(code) and not _seat_special_eligibility(code) for code in raw_codes):
            options.append({"value": f"LADIES|{family}", "label": f"{label} - woman candidate", "group": "Reservation category"})

    # The DSE CSV retains NT-A/B/C/D.  The FE CSV currently contains generic
    # GNT/LNT records, so it can only offer NT without pretending a subtype.
    nt_subtypes = [suffix for suffix in ("A", "B", "C", "D") if any(
        code.startswith((f"GNT{suffix}", f"LNT{suffix}")) for code in raw_codes
    )]
    if nt_subtypes:
        for suffix in nt_subtypes:
            options.append({"value": f"NT-{suffix}", "label": f"NT-{suffix}", "group": "Reservation category"})
            if any(code.startswith(f"LNT{suffix}") for code in raw_codes):
                options.append({"value": f"LADIES|NT-{suffix}", "label": f"NT-{suffix} - woman candidate", "group": "Reservation category"})
    elif "NT" in base_families:
        options.append({"value": "NT", "label": "NT (as recorded in the cutoff data)", "group": "Reservation category"})
        if any(_seat_base_family(code) == "NT" and _seat_is_ladies(code) for code in raw_codes):
            options.append({"value": "LADIES|NT", "label": "NT - woman candidate", "group": "Reservation category"})

    special_labels = {
        "DEFENCE": "Defence", "PWD": "Persons with Disability (PwD)",
        "ORPHAN": "Orphan", "TFWS": "Tuition Fee Waiver Scheme (TFWS)", "MINORITY": "Minority",
    }
    for special, label in special_labels.items():
        matching = [code for code in raw_codes if _seat_special_eligibility(code) == special]
        for family in sorted({_seat_base_family(code) for code in matching if _seat_base_family(code)}):
            family_label = "Open" if family == "OPEN" else family.replace("VJ", "VJ/DT")
            options.append({"value": f"{special}|{family}", "label": f"{label} - {family_label} (document required)", "group": "Special eligibility"})
        if matching and not any(_seat_base_family(code) for code in matching):
            options.append({"value": f"{special}|OPEN", "label": f"{label} (document required)", "group": "Special eligibility"})
    return options

# Build the reverse-lookup expansion table: every raw seat code → its full alias group.
# The legacy key 'VJNT' is preserved as an alias for VJ_DT for backward compat.
_REVERSE_CATEGORY_EXPANSIONS: dict[str, list[str]] = {}
for _canonical, _variants in CATEGORY_MAP.items():
    for _v in _variants:
        _REVERSE_CATEGORY_EXPANSIONS[_v.upper()] = _variants

CATEGORY_EXPANSIONS: Mapping[str, list[str]] = MappingProxyType(
    {
        **{k: v for k, v in CATEGORY_MAP.items()},
        # backward-compat aliases for any raw seat codes that arrive from the DB
        **_REVERSE_CATEGORY_EXPANSIONS,
        # legacy combined key kept for any callers that pre-date the split
        'VJNT': CATEGORY_MAP['VJ_DT'],
    }
)

# Soft match aliases only — stored branch strings are never rewritten to these.
BRANCH_MATCH_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "aiml": ("AIML", "AI ML", "AI & ML", "Artificial Intelligence and Machine Learning", "Artificial Intelligence & Machine Learning"),
        "aids": ("AIDS", "AI DS", "AI & DS", "Artificial Intelligence and Data Science", "Artificial Intelligence & Data Science"),
        "cse": ("CSE", "Computer Science and Engineering", "Computer Science & Engineering", "Computer Engineering"),
        "it": ("IT", "Information Technology"),
        "cyber security": ("Cyber Security", "Cybersecurity"),
        "extc": ("EXTC", "Electronics and Telecommunication", "Electronics & Telecommunication"),
        "mechanical": ("Mechanical", "Mechanical Engineering"),
        "civil": ("Civil", "Civil Engineering"),
    }
)

REGIONAL_FALLBACK_MIN = 15
SAFE_COLOR = "#00FF66"
TARGET_COLOR = "#FFB300"
DREAM_COLOR = "#FF3366"


class PredictRequest(BaseModel):
    """Unified schema for MHT-CET (FE) and Diploma (DSE) prediction requests."""
    percentile: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    percentage: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    category: str = Field(default="OPEN")
    eligible_for_ladies: bool = Field(default=False)
    special_eligibilities: list[str] = Field(default_factory=list)
    university_scope: str = Field(default="ANY")
    branch: Optional[str] = Field(default="All")
    city: Optional[str] = Field(default="All")
    college_type: Optional[str] = Field(default="All")
    pathway: str = Field(default="fe")
    extra_filters: Optional[dict[str, Any]] = None


def _empty_response(pathway: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "pathway": pathway,
        "total_found": 0,
        "unique_college_count": 0,
        "safe_zone": [],
        "target_zone": [],
        "dream_zone": [],
        "results": [],
        "recommendations": [],
        "status": "success",
        "regional_fallback": False,
    }
    payload.update(extra)
    return payload


def _literal_contains(values: pd.Series, query: str) -> pd.Series:
    return values.astype("string").str.contains(re.escape(query), case=False, regex=True, na=False)


def _branch_query_terms(branch_query: str) -> list[str]:
    q = branch_query.strip()
    terms = [q]
    alias_key = q.casefold()
    if alias_key in BRANCH_MATCH_ALIASES:
        terms.extend(BRANCH_MATCH_ALIASES[alias_key])
    else:
        for key, aliases in BRANCH_MATCH_ALIASES.items():
            if key in alias_key or any(alias.casefold() in alias_key for alias in aliases):
                terms.extend(aliases)
                break
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        folded = term.casefold()
        if folded in seen or not term.strip():
            continue
        seen.add(folded)
        unique.append(term.strip())
    return unique


def _match_branch(series: pd.Series, branch_query: Optional[str]) -> pd.Series:
    """Prefer an exact CSV branch match; use aliases only for free-text input."""
    if not branch_query or branch_query.strip().casefold() in ("all", "all branches", ""):
        return pd.Series(True, index=series.index)

    text = series.astype("string")
    query = branch_query.strip()
    exact = text.str.casefold().eq(query.casefold())
    if exact.any():
        return exact

    # Abbreviations such as CSE are useful for API/free-text callers, but must
    # never broaden a value chosen from the data-backed branch dropdown.
    mask = pd.Series(False, index=series.index)
    for term in _branch_query_terms(query):
        mask = mask | text.str.contains(re.escape(term), case=False, regex=True, na=False)
    return mask


def _match_category_or_seat_code(
    frame: pd.DataFrame,
    candidate_category: Optional[str],
    seat_code: Optional[str] = None,
    *,
    eligible_for_ladies: bool = False,
    special_eligibilities: Optional[list[str]] = None,
    university_scope: Optional[str] = None,
) -> pd.DataFrame:
    """Select applicable raw CAP records without exposing CAP codes to students.

    A Maharashtra candidate competes for Open records as well as their own
    reserved family.  Ladies and special reservations are included only after
    the student supplies that eligibility.  Each matching row remains a
    separate CSV fact (including H/O/S, round and year).
    """
    codes = frame[COL_SEAT_CODE].astype("string").fillna("").str.upper()
    if seat_code and seat_code.strip().casefold() not in ("all", "", "any seat code"):
        return frame.loc[codes.eq(seat_code.strip().upper())]

    q = str(candidate_category or "OPEN").strip().upper()
    # A raw CAP code remains a supported compatibility input, except when it
    # collides with a student-facing reservation category (notably ``EWS``).
    # In that case EWS means the candidate category, not an instruction to
    # hide merit-based Open records.
    if q in set(codes.unique()) and q not in _STUDENT_CATEGORY_TO_FAMILY:
        # Backward compatible requests that passed a seat code through category.
        return frame.loc[codes.eq(q)]
    requested_family, selected_special = _parse_student_category(q)
    eligible_special = {str(item).strip().upper() for item in (special_eligibilities or [])} | selected_special
    eligible_for_ladies = eligible_for_ladies or "LADIES" in eligible_special
    scope = str(university_scope or "ANY").strip().upper()

    def applicable(code: str) -> bool:
        special = _seat_special_eligibility(code)
        if special:
            if special not in eligible_special:
                return False
        elif _seat_is_ladies(code) and not eligible_for_ladies:
            return False

        seat_scope = _seat_university_scope(code)
        if scope in {"HOME", "OTHER"} and seat_scope in {"HOME", "OTHER"} and seat_scope != scope:
            return False

        base = _seat_base_family(code)
        if special in {"ORPHAN", "TFWS", "MINORITY"}:
            # These records have no CSV reservation-family suffix.
            return True
        return base in {"OPEN", requested_family}

    mask = codes.map(applicable)
    return frame.loc[mask]


def _adjacent_cities(city_name: str) -> list[str]:
    if city_name in ADJACENT_REGIONS:
        return list(ADJACENT_REGIONS[city_name])
    folded = city_name.casefold()
    for key, neighbors in ADJACENT_REGIONS.items():
        if key.casefold() == folded:
            return list(neighbors)
    return [city_name]


def _score_and_serialize(
    data_loader: DataLoader,
    frame: pd.DataFrame,
    candidate_score: float,
    clean_pathway: str,
    regional_fallback: bool,
    candidate_category: Optional[str],
) -> dict[str, Any]:
    if frame.empty:
        return _empty_response(clean_pathway, regional_fallback=regional_fallback)

    # Each source row is an admission fact.  Do not merge years, rounds, or
    # seat codes into a synthetic forecast or an arbitrary latest cutoff.
    merged = frame.copy()
    merged["cutoff"] = pd.to_numeric(merged[COL_CUTOFF], errors="coerce").round(2)
    merged["previous_cutoff"] = merged["cutoff"]
    merged["predicted_cutoff"] = merged["cutoff"]
    merged["forecast_2026"] = merged["cutoff"]
    merged["predicted_2026"] = merged["cutoff"]

    s = float(candidate_score)
    delta = s - merged["cutoff"].astype(float)
    merged["probability"] = np.clip((1.0 / (1.0 + np.exp(-0.55 * delta))) * 100.0, 1.0, 99.0).round(1)
    merged["probability_percent"] = merged["probability"].round().astype(int)
    merged["score_diff"] = delta.round(2)

    c26 = merged["cutoff"].astype(float)
    p = merged["probability"].astype(float)
    is_safe = (c26 <= s) | (p >= 75.0)
    is_target = ~is_safe & (((c26 > s) & (c26 <= s + 3.5)) | ((p >= 40.0) & (p < 75.0)))
    is_dream = ~is_safe & ~is_target

    merged["status"] = np.select([is_safe, is_target, is_dream], ["SAFE", "MODERATE", "DREAM"], default="DREAM")
    merged["badge"] = np.select([is_safe, is_target], ["SAFE", "MODERATE"], default="DREAM")
    merged["classification"] = np.select([is_safe, is_target], ["Safe", "Moderate"], default="Dream")
    merged["color"] = np.select([is_safe, is_target], [SAFE_COLOR, TARGET_COLOR], default=DREAM_COLOR)
    merged["accent_color"] = merged["color"]
    merged["probability_label"] = np.select(
        [is_safe, is_target],
        ["High Chance", "Moderate Chance"],
        default="Low / Reach Chance",
    )

    inst_db = data_loader.institutes_data or {}

    def enrich_metadata(row: pd.Series) -> pd.Series:
        code = str(row.get(COL_COLLEGE_CODE) or "").strip().zfill(5)
        inst = inst_db.get(code, {}) if isinstance(inst_db.get(code, {}), dict) else {}
        overview = inst.get("system_overview") if isinstance(inst.get("system_overview"), dict) else {}
        logistics = inst.get("administration_logistics") if isinstance(inst.get("administration_logistics"), dict) else {}
        placements = inst.get("placement_matrix") if isinstance(inst.get("placement_matrix"), dict) else {}
        # Only expose enrichment recorded in the bundled institute data.  Do
        # not manufacture fees, accreditation, hostel, or placement figures.
        unavailable = "Not available in bundled data"
        fees = inst.get("open_fees") or logistics.get("estimated_open_fees") or unavailable
        naac = inst.get("naac_grade") or overview.get("accreditation") or unavailable
        hostel = inst.get("hostel") or logistics.get("hostel_availability") or unavailable
        avg_pkg = inst.get("avg_package") or placements.get("average_package") or unavailable
        highest_pkg = inst.get("highest_package") or placements.get("highest_package") or unavailable
        status_name = inst.get("status") or overview.get("status") or row.get(COL_TYPE) or "Un-Aided"
        return pd.Series(
            {
                "status_name": status_name,
                "open_fees": fees,
                "fees": fees,
                "naac_grade": naac,
                "naac": naac,
                "hostel": hostel,
                "avg_package": avg_pkg,
                "avg_pkg": avg_pkg,
                "highest_package": highest_pkg,
                "highest_pkg": highest_pkg,
            }
        )

    enriched = merged.apply(enrich_metadata, axis=1)
    full_df = pd.concat([merged, enriched], axis=1)
    full_df["dte_code"] = full_df[COL_COLLEGE_CODE].astype(str).str.strip().str.zfill(5)
    full_df["location"] = full_df[COL_CITY]
    full_df["prev_cutoff"] = full_df["previous_cutoff"]
    full_df["college_type"] = full_df[COL_TYPE]
    full_df["institute_type"] = full_df[COL_TYPE]
    full_df["category_used"] = full_df[COL_CATEGORY]
    full_df["seat_context"] = full_df[COL_SEAT_CODE].map(lambda code: _seat_applicability_reason(str(code), candidate_category))

    safe_df = full_df.loc[full_df["status"] == "SAFE"].sort_values(
        ["forecast_2026", "previous_cutoff"], ascending=[False, False], kind="stable"
    ).reset_index(drop=True)
    target_df = full_df.loc[full_df["status"] == "MODERATE"].sort_values(
        ["forecast_2026", "score_diff"], ascending=[True, False], kind="stable"
    ).reset_index(drop=True)
    dream_df = full_df.loc[full_df["status"] == "DREAM"].sort_values(
        ["forecast_2026"], ascending=[True], kind="stable"
    ).reset_index(drop=True)

    safe_df["rank"] = safe_df.index + 1
    target_df["rank"] = target_df.index + 1
    dream_df["rank"] = dream_df.index + 1

    combined_df = pd.concat([safe_df, target_df, dream_df], ignore_index=True)
    combined_df["rank"] = combined_df.index + 1

    def clean_records(df_slice: pd.DataFrame) -> list[dict[str, Any]]:
        if df_slice.empty:
            return []
        cols = [
            "rank", "dte_code", COL_COLLEGE_CODE, COL_COLLEGE_NAME, COL_BRANCH, COL_CITY, "location",
            COL_SEAT_CODE, COL_CATEGORY, COL_ROUND, "college_type", "institute_type", "category_used", "prev_cutoff", "previous_cutoff",
            "forecast_2026", "predicted_2026", "predicted_cutoff", "cutoff", "probability",
            "probability_percent", "probability_label", "classification", "status", "badge",
            "color", "accent_color", "score_diff", "seat_context", "fees", "open_fees", "naac", "naac_grade",
            "hostel", "avg_pkg", "avg_package", "highest_pkg", "highest_package", COL_YEAR,
            "historical_record_count",
        ]
        avail = [c for c in cols if c in df_slice.columns]
        return df_slice[avail].replace({np.nan: None, pd.NA: None}).to_dict(orient="records")

    safe_records = clean_records(safe_df)
    target_records = clean_records(target_df)
    dream_records = clean_records(dream_df)
    all_records = clean_records(combined_df)

    # A recommendation is one institute, chosen from its applicable historical
    # records.  The complete CSV facts remain in ``results`` for audit/export.
    # Prefer the closest achievable cutoff; if none is achievable, prefer the
    # closest competitive one.  Do not aggregate, alter or discard source rows.
    display_rank = {"SAFE": 3, "MODERATE": 2, "DREAM": 1}
    display_df = full_df.copy()
    display_df["_display_rank"] = display_df["status"].map(display_rank).fillna(0)
    display_df["_distance"] = display_df["score_diff"].abs()
    display_df = display_df.sort_values(
        ["_display_rank", "_distance", "forecast_2026"], ascending=[False, True, False], kind="stable"
    )
    display_df["historical_record_count"] = display_df.groupby(COL_COLLEGE_CODE)[COL_COLLEGE_CODE].transform("size")
    display_df = display_df.drop_duplicates(subset=[COL_COLLEGE_CODE], keep="first").reset_index(drop=True)
    display_df["rank"] = display_df.index + 1
    recommendations = clean_records(display_df)

    # Keep every raw, applicable CAP fact attached to the college-level card.
    # ``results`` remains the flat audit/export view, while this association
    # gives callers a direct way to inspect the rows represented by a card.
    records_by_college: dict[str, list[dict[str, Any]]] = {}
    for record in all_records:
        code = str(record.get("dte_code") or record.get(COL_COLLEGE_CODE) or "").strip().zfill(5)
        records_by_college.setdefault(code, []).append(record)
    for recommendation in recommendations:
        code = str(recommendation.get("dte_code") or recommendation.get(COL_COLLEGE_CODE) or "").strip().zfill(5)
        recommendation["historical_records"] = records_by_college.get(code, [])
        recommendation["historical_record_count"] = len(recommendation["historical_records"])

    return {
        "pathway": clean_pathway,
        "candidate_score": float(candidate_score),
        "total_found": len(all_records),
        "unique_college_count": len(recommendations),
        "safe_zone": safe_records,
        "target_zone": target_records,
        "dream_zone": dream_records,
        "results": all_records,
        "recommendations": recommendations,
        "status": "success",
        "regional_fallback": regional_fallback,
    }


def _execute_prediction_engine(
    data_loader: DataLoader,
    data: pd.DataFrame,
    exam_type: str,
    candidate_score: float,
    category: str,
    seat_code: Optional[str] = None,
    eligible_for_ladies: bool = False,
    special_eligibilities: Optional[list[str]] = None,
    university_scope: Optional[str] = None,
    branch: Optional[str] = None,
    city: Optional[str] = None,
    college_type: Optional[str] = None,
    year: Optional[int] = None,
    round_name: Optional[str] = None,
    pathway: str = "fe",
    extra_filters: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Uncapped dual-pathway engine: filters → 2026 forecast → logistic P → zones."""
    del extra_filters
    clean_pathway = str(pathway or "fe").strip().lower()
    if clean_pathway == "diploma":
        clean_pathway = "dse"

    if not np.isfinite(candidate_score) or not 0.0 <= candidate_score <= 100.0:
        return _empty_response(clean_pathway)

    if clean_pathway == "dse":
        frame = data.loc[data[COL_EXAM_TYPE].isin(["DIPLOMA", "DSE"])].copy()
    else:
        frame = data.loc[data[COL_EXAM_TYPE].eq("CET")].copy()

    if frame.empty:
        log.warning("No records found for pathway=%s, exam_type=%s", clean_pathway, exam_type)
        return _empty_response(clean_pathway)

    frame = _match_category_or_seat_code(
        frame, category, seat_code,
        eligible_for_ladies=eligible_for_ladies,
        special_eligibilities=special_eligibilities,
        university_scope=university_scope,
    )
    frame = frame.loc[_match_branch(frame[COL_BRANCH], branch)]

    if year is not None:
        frame = frame.loc[frame[COL_YEAR].eq(int(year))]
    if round_name and round_name.strip().casefold() not in ("all", ""):
        frame = frame.loc[frame[COL_ROUND].astype("string").fillna("").str.casefold().eq(round_name.strip().casefold())]

    if college_type and college_type.strip().casefold() not in ("all", "any type", "all types", ""):
        type_mask = _literal_contains(frame[COL_TYPE], college_type.strip())
        frame = frame.loc[type_mask]

    regional_fallback = False
    if city and city.strip().casefold() not in ("all", "all cities", ""):
        resolved_city = _clean_and_resolve_city(city.strip()) or city.strip()
        city_frame = frame.loc[frame[COL_CITY].eq(resolved_city)]
        # Decide proximity expansion from actual institutes, before a
        # representative historical record is selected for each card.  CAP
        # rows are retained unchanged by the serializer below.
        exact_college_count = city_frame[COL_COLLEGE_CODE].nunique()
        result_cities = [resolved_city]
        if exact_college_count < REGIONAL_FALLBACK_MIN:
            nearby_cities = _adjacent_cities(resolved_city)
            expanded_frame = frame.loc[frame[COL_CITY].isin(nearby_cities)]
            # Mark an expansion only when the mapping genuinely widens the
            # result set; an unmapped city must stay an exact-city search.
            if len(nearby_cities) > 1:
                city_frame = expanded_frame
                regional_fallback = True
                result_cities = nearby_cities
        response = _score_and_serialize(
            data_loader, city_frame, candidate_score, clean_pathway, regional_fallback, category
        )
        # Keep the geographic scope alongside the serialized recommendation
        # list.  This makes it explicit to every API/UI consumer that cards
        # came from the expanded frame, not the original exact-city subset.
        response["exact_city_unique_college_count"] = int(exact_college_count)
        response["expanded_unique_college_count"] = int(response["unique_college_count"])
        response["result_cities"] = result_cities
        return response

    return _score_and_serialize(data_loader, frame, candidate_score, clean_pathway, regional_fallback, category)


class CETPredictor:
    """Returns deterministic eligibility bands from validated percentile and percentage history."""

    def __init__(self, data_loader: DataLoader) -> None:
        self.data_loader = data_loader
        self.data = self.data_loader.get_all_data()
        log.info("CET predictor initialized with %d canonical rows.", len(self.data))

    def calculate_probability(self, user_val: float, cutoff_val: float) -> tuple[str, int]:
        delta = float(user_val) - float(cutoff_val)
        prob = int(np.rint(np.clip((1.0 / (1.0 + np.exp(-0.55 * delta))) * 100.0, 1.0, 99.0)))
        label = "High Chance" if prob >= 75 else ("Moderate Chance" if prob >= 40 else "Low / Reach Chance")
        return label, prob

    def predict(
        self,
        percentile: float,
        category: str = "OPEN",
        seat_code: Optional[str] = None,
        eligible_for_ladies: bool = False,
        special_eligibilities: Optional[list[str]] = None,
        university_scope: Optional[str] = None,
        branch: Optional[str] = None,
        city: Optional[str] = None,
        college_type: Optional[str] = None,
        year: Optional[int] = None,
        round_name: Optional[str] = None,
        extra_filters: Optional[Mapping[str, Any]] = None,
        pathway: str = "fe",
    ) -> list[dict[str, Any]] | dict[str, Any]:
        clean_pathway = str(pathway or "fe").strip().lower()
        exam_type = "DIPLOMA" if clean_pathway in ("dse", "diploma") else "CET"
        dataset = self.data_loader.get_data_by_pathway(clean_pathway)

        resp = _execute_prediction_engine(
            data_loader=self.data_loader,
            data=dataset,
            exam_type=exam_type,
            candidate_score=float(percentile),
            category=category,
            seat_code=seat_code,
            eligible_for_ladies=eligible_for_ladies,
            special_eligibilities=special_eligibilities,
            university_scope=university_scope,
            branch=branch,
            city=city,
            college_type=college_type,
            year=year,
            round_name=round_name,
            pathway=clean_pathway,
            extra_filters=extra_filters,
        )
        return resp


class DiplomaPredictor:
    """Predictor dedicated to Diploma / DSE students."""

    def __init__(self, data_loader: DataLoader) -> None:
        self.data_loader = data_loader
        self.data = self.data_loader.get_combined_diploma_data()
        log.info("Diploma predictor initialized with %d canonical rows.", len(self.data))

    def calculate_probability(self, user_val: float, cutoff_val: float) -> tuple[str, int]:
        delta = float(user_val) - float(cutoff_val)
        prob = int(np.rint(np.clip((1.0 / (1.0 + np.exp(-0.55 * delta))) * 100.0, 1.0, 99.0)))
        label = "High Chance" if prob >= 75 else ("Moderate Chance" if prob >= 40 else "Low / Reach Chance")
        return label, prob

    def predict(
        self,
        percentage: float,
        category: str = "OPEN",
        seat_code: Optional[str] = None,
        eligible_for_ladies: bool = False,
        special_eligibilities: Optional[list[str]] = None,
        university_scope: Optional[str] = None,
        branch: Optional[str] = None,
        city: Optional[str] = None,
        college_type: Optional[str] = None,
        year: Optional[int] = None,
        round_name: Optional[str] = None,
        extra_filters: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        return _execute_prediction_engine(
            data_loader=self.data_loader,
            data=self.data_loader.get_combined_diploma_data(),
            exam_type="DIPLOMA",
            candidate_score=float(percentage),
            category=category,
            seat_code=seat_code,
            eligible_for_ladies=eligible_for_ladies,
            special_eligibilities=special_eligibilities,
            university_scope=university_scope,
            branch=branch,
            city=city,
            college_type=college_type,
            year=year,
            round_name=round_name,
            pathway="dse",
            extra_filters=extra_filters,
        )
