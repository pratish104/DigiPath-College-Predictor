"""Strict, manifest-driven admissions and job data ingestion for DigiPath.

Provides:
  - Strict Maharashtra District Whitelist sanitation (35 canonical districts)
  - Safe regex cleaning of junk noise tokens while preserving all authentic engineering branches
  - Standardized college types ('Government', 'Government Aided', 'Autonomous',
    'Un-Aided Autonomous', 'Un-Aided Non-Autonomous')
  - Unified seat codes and categories
  - Zero-padded 5-digit DTE code resolution
  - Dual-pathway dataset loading for MHT-CET (FE) and Diploma / DSE
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

log = logging.getLogger("digipath.data_loader")

# ── Canonical Schema Columns ──────────────────────────────────────────────────
COL_COLLEGE_NAME = "college_name"
COL_COLLEGE_CODE = "college_code"
COL_CITY = "city"
COL_BRANCH = "branch"
COL_CATEGORY = "category"
COL_CUTOFF = "cutoff_value"
COL_YEAR = "year"
COL_TYPE = "college_type"
COL_EXAM_TYPE = "exam_type"
COL_QUOTA = "quota"
COL_SEAT_TYPE = "seat_type"
COL_SEAT_CODE = "seat_code"
COL_ROUND = "round"
COL_STATE_RANK = "state_rank"

# ── Manifest & Data Ingestion Specs ───────────────────────────────────────────
MANIFEST: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "fe_2024.csv": MappingProxyType({"exam_type": "CET", "year": 2024, "metric": "percentile", "pathway": "fe"}),
        "fe_2025.csv": MappingProxyType({"exam_type": "CET", "year": 2025, "metric": "percentile", "pathway": "fe"}),
        "converted_college_data_final.csv": MappingProxyType({"exam_type": "DIPLOMA", "year": 2024, "metric": "percentage", "pathway": "dse"}),
        "CAP_Cutoff_Data.csv": MappingProxyType({"exam_type": "DIPLOMA", "year": 2025, "metric": "percentage", "pathway": "dse"}),
    }
)

# Case-insensitive on-disk aliases so missing canonical names still bind.
FILE_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "fe_2024.csv": ("fe_2024.csv", "FE_2024.csv"),
        "fe_2025.csv": ("fe_2025.csv", "FE_2025.csv"),
        "converted_college_data_final.csv": ("converted_college_data_final.csv",),
        "CAP_Cutoff_Data.csv": ("CAP_Cutoff_Data.csv", "cap_cutoff_data.csv"),
    }
)

CORE_BRANCH_OPTIONS: tuple[str, ...] = (
    "AIML",
    "AIDS",
    "Computer Engineering",
    "Computer Science and Engineering",
    "CSE",
    "Information Technology",
    "IT",
    "Cyber Security",
    "Electronics and Telecommunication Engineering",
    "EXTC",
    "Mechanical Engineering",
    "Civil Engineering",
    "Electrical Engineering",
    "Chemical Engineering",
    "Instrumentation Engineering",
    "Robotics and Automation",
    "Biomedical Engineering",
)

REJECTED_DATASETS = frozenset(
    {
        "merged_data.csv",
        "final_clean_full.csv",
    }
)

_COLUMN_ALIASES: Mapping[str, Sequence[str]] = MappingProxyType(
    {
        COL_COLLEGE_NAME: ("college name", "institute name", "collegename", "institutename", "college_name", "institute_name"),
        COL_COLLEGE_CODE: ("college code", "dte code", "college_code", "college_co", "choice code", "choice_code", "dte_code", "inst code", "institute code"),
        COL_CITY: ("city", "location", "district", "place", "region"),
        COL_BRANCH: ("branch", "course name", "course", "branch_name", "branch name", "stream"),
        COL_CATEGORY: ("category", "seat category", "seat_category", "candidate category", "caste category"),
        COL_CUTOFF: ("cutoff", "percentile", "percentage", "percent", "cutoff_value", "score", "marks", "cutoff %"),
        COL_QUOTA: ("quota", "seat_quota", "home_other_state"),
        COL_SEAT_TYPE: ("seat type", "gender", "seat_type", "type_of_seat"),
        COL_ROUND: ("round", "stage", "cap round", "cap_round"),
        COL_STATE_RANK: ("cutoff rank", "state rank", "rank", "state_rank", "merit_no", "merit no"),
    }
)

# ── 1. CITY / DISTRICT SANITATION SPEC (35 Canonical Districts) ───────────────
MAHARASHTRA_DISTRICTS: list[str] = [
    "Mumbai", "Thane", "Navi Mumbai", "Pune", "Nashik", "Nagpur",
    "Chhatrapati Sambhajinagar", "Solapur", "Kolhapur", "Amravati",
    "Nanded", "Sangli", "Satara", "Ahmednagar", "Akola", "Jalgaon",
    "Latur", "Dhule", "Palghar", "Raigad", "Ratnagiri", "Sindhudurg",
    "Chandrapur", "Yavatmal", "Buldhana", "Beed", "Gondia", "Bhandara",
    "Gadchiroli", "Hingoli", "Jalna", "Dharashiv", "Parbhani", "Washim", "Wardha",
]

_MAHA_DISTRICT_SET = frozenset(MAHARASHTRA_DISTRICTS) | {"Osmanabad"}
_DISTRICT_LOWER_MAP: Mapping[str, str] = {d.lower(): d for d in MAHARASHTRA_DISTRICTS}
_DISTRICT_LOWER_MAP["osmanabad"] = "Dharashiv"

# Fuzzy Town / Taluka / Sub-region to Administrative District Master Mapping
_TOWN_TO_DISTRICT_MAP: Mapping[str, str] = MappingProxyType(
    {
        # Mumbai
        "mumbai": "Mumbai", "andheri": "Mumbai", "bandra": "Mumbai", "kurla": "Mumbai",
        "dadar": "Mumbai", "borivali": "Mumbai", "chembur": "Mumbai", "ghatkopar": "Mumbai",
        "kandivali": "Mumbai", "malad": "Mumbai", "powai": "Mumbai", "sion": "Mumbai",
        "wadala": "Mumbai", "worli": "Mumbai", "k j somaiya institute of technology": "Mumbai",

        # Navi Mumbai
        "navi mumbai": "Navi Mumbai", "navimumbai": "Navi Mumbai", "vashi": "Navi Mumbai",
        "kharghar": "Navi Mumbai", "kharghar navi mumbai": "Navi Mumbai", "panvel": "Navi Mumbai",
        "new panvel": "Navi Mumbai", "airoli": "Navi Mumbai", "nerul": "Navi Mumbai",
        "belapur": "Navi Mumbai", "ghansoli": "Navi Mumbai", "koparkhairane": "Navi Mumbai",

        # Thane
        "thane": "Thane", "dist thane": "Thane", "dist.thane": "Thane", "thane (e)": "Thane",
        "thane (w)": "Thane", "badlapur": "Thane", "badlapur(w)": "Thane", "kalyan": "Thane",
        "bapsai tal.kalyan": "Thane", "ulhasnagar": "Thane", "bhayinder": "Thane",
        "bhayandar": "Thane", "mira bhayandar": "Thane", "shahpur.": "Thane",
        "tal. shahapur": "Thane", "tal-ambernath.": "Thane", "ambernath": "Thane",
        "dombivli": "Thane", "mumbra": "Thane", "bhiwandi": "Thane",

        # Palghar
        "palghar": "Palghar", "boisar": "Palghar", "vasai": "Palghar", "virar": "Palghar",
        "kaman dist. palghar": "Palghar", "pal": "Palghar",

        # Raigad
        "raigad": "Raigad", "raigad.": "Raigad", "lonere": "Raigad", "karjat": "Raigad",
        "khalapur dist raigad": "Raigad", "tal. khalapur. dist. raigad": "Raigad",
        "yadavrao tasgaonkar college of engineering & management": "Raigad",
        "alibag": "Raigad", "mahad": "Raigad", "mangaon": "Raigad", "roha": "Raigad", "pen": "Raigad",

        # Pune
        "pune": "Pune", "pune.": "Pune", "dist-pune": "Pune", "isbm college of engineering pune": "Pune",
        "coep technological university": "Pune", "baramati": "Pune", "baramati dist.pune": "Pune",
        "malegaon-baramati": "Pune", "lonavala": "Pune", "talegaon": "Pune", "ravet": "Pune",
        "wagholi": "Pune", "narhe": "Pune", "pisoli": "Pune", "sasewadi": "Pune", "bhor": "Pune",
        "haveli": "Pune", "tal. haveli": "Pune", "tal. indapur": "Pune", "indapur": "Pune",
        "dumbarwadi": "Pune", "avasari khurd": "Pune", "post belhe tal. junnar dist. pune": "Pune",
        "junnar": "Pune", "swami - chincholi tal. daund dist. pune": "Pune", "daund": "Pune",
        "kuran": "Pune", "someshwar nagar": "Pune", "bhima": "Pune",
        "navsahyadri education societys group of institutions": "Pune", "shirgaon": "Pune",

        # Nashik
        "nashik": "Nashik", "nashik.": "Nashik", "(nashik)": "Nashik", "adgaon nashik": "Nashik",
        "agaskhind tal. sinnar": "Nashik", "sinnar": "Nashik", "chincholi dist. nashik": "Nashik",
        "malegaon.": "Nashik", "malegaon": "Nashik", "yeola": "Nashik", "trimbak": "Nashik",
        "niphad": "Nashik", "dindori": "Nashik", "chandwad": "Nashik",

        # Nagpur
        "nagpur": "Nagpur", "ramtek": "Nagpur", "tal. hingna hingna nagpur": "Nagpur",
        "hingna": "Nagpur", "kamthi": "Nagpur", "umred": "Nagpur",

        # Chhatrapati Sambhajinagar
        "chhatrapati sambhajinagar": "Chhatrapati Sambhajinagar", "aurangabad": "Chhatrapati Sambhajinagar",
        "ohar": "Chhatrapati Sambhajinagar",
        "international centre of excellence in engineering and management": "Chhatrapati Sambhajinagar",
        "paithan": "Chhatrapati Sambhajinagar", "gangapur": "Chhatrapati Sambhajinagar",
        "vaijapur": "Chhatrapati Sambhajinagar", "kannad": "Chhatrapati Sambhajinagar",

        # Solapur
        "solapur": "Solapur", "solapur.": "Solapur", "barshi": "Solapur", "pandharpur": "Solapur",
        "korti tal. pandharpur dist solapur": "Solapur", "sangola": "Solapur", "akluj": "Solapur",
        "paniv": "Solapur", "shree siddheshwar womens college of engineering solapur.": "Solapur",
        "mangalwedha": "Solapur", "mohol": "Solapur", "karmala": "Solapur", "madha": "Solapur",

        # Kolhapur
        "kolhapur": "Kolhapur", "kolhapur.": "Kolhapur", "ichalkaranji.": "Kolhapur",
        "ichalkaranji": "Kolhapur", "jaysingpur": "Kolhapur", "gadhinglaj": "Kolhapur",
        "panhala": "Kolhapur", "warananagar": "Kolhapur", "yadrav": "Kolhapur",
        "yadrav(ichalkaranji)": "Kolhapur", "ashokrao mane group of institutions": "Kolhapur",
        "sanjay ghodawat institute": "Kolhapur", "hatkanangle": "Kolhapur", "shirol": "Kolhapur",

        # Sangli
        "sangli": "Sangli", "sangli.": "Sangli", "miraj": "Sangli",
        "kille macchindragad tal. walva district- sangali": "Sangli", "walva": "Sangli",
        "islampur": "Sangli", "urun islampur": "Sangli", "tasgaon": "Sangli", "vita": "Sangli",

        # Satara
        "satara": "Satara", "satara.": "Satara", "karad": "Satara",
        "phaltan education societys college of engineering thakurki tal- phaltan dist-satara": "Satara",
        "phaltan": "Satara", "wadwadi": "Satara", "wai": "Satara", "mahabaleshwar": "Satara",
        "koregaon": "Satara", "shirwal": "Satara",

        # Ahmednagar
        "ahmednagar": "Ahmednagar", "ahmednagar.": "Ahmednagar", "dist.ahmednagar": "Ahmednagar",
        "chas dist. ahmednagar": "Ahmednagar", "sangamner": "Ahmednagar", "bota sangamner": "Ahmednagar",
        "kopargaon": "Ahmednagar", "nepti": "Ahmednagar",
        "dr. v.k. patil college of engineering & technology": "Ahmednagar", "shrirampur": "Ahmednagar",
        "rahata": "Ahmednagar", "rahuri": "Ahmednagar", "shrigonda": "Ahmednagar", "kashti": "Ahmednagar",

        # Amravati
        "amravati": "Amravati", "badnera": "Amravati", "achalpur": "Amravati", "morshi": "Amravati",

        # Akola
        "akola": "Akola", "balapur": "Akola", "patur": "Akola", "murtizapur": "Akola", "akot": "Akola",

        # Buldhana
        "buldhana": "Buldhana", "shegaon": "Buldhana", "shegaon.": "Buldhana", "444302": "Buldhana",
        "chikhali": "Buldhana", "babulgaon": "Buldhana", "yelgaon": "Buldhana", "nile": "Buldhana",
        "janata shikshan prasarak mandal": "Buldhana", "khamgaon": "Buldhana", "malkapur": "Buldhana",

        # Jalgaon
        "jalgaon": "Jalgaon", "bhusawal": "Jalgaon", "faizpur": "Jalgaon", "chopda": "Jalgaon",
        "amalner": "Jalgaon", "chalisgaon": "Jalgaon",

        # Dhule / Nandurbar
        "dhule": "Dhule", "tal dist dhule": "Dhule", "shirpur": "Dhule", "dondaicha.": "Dhule",
        "dondaicha": "Dhule", "sakri": "Dhule", "sindkheda": "Dhule", "nandurbar": "Dhule",
        "dist. nandurbar": "Dhule", "akkalkuwa": "Dhule",

        # Nanded
        "nanded": "Nanded", "nanded.": "Nanded",
        "gramin technical and management campus nanded.": "Nanded", "degloor": "Nanded", "kinwat": "Nanded",

        # Latur
        "latur": "Latur", "latur.": "Latur", "udgir": "Latur", "ausa": "Latur", "nilanga": "Latur",

        # Dharashiv / Osmanabad
        "dharashiv": "Dharashiv", "osmanabad": "Dharashiv", "tuljapur": "Dharashiv", "omerga": "Dharashiv",

        # Beed
        "beed": "Beed", "ambejogai": "Beed", "parli": "Beed", "majalgaon": "Beed",

        # Chandrapur
        "chandrapur": "Chandrapur", "bhadrawati": "Chandrapur", "badravati": "Chandrapur",
        "rajiv gandhi college of engineering research & technology chandrapur": "Chandrapur",
        "warora": "Chandrapur", "ballarpur": "Chandrapur",

        # Yavatmal
        "yavatmal": "Yavatmal", "mouza bamni": "Yavatmal", "pusad": "Yavatmal", "umarkhed": "Yavatmal",

        # Wardha
        "wardha": "Wardha", "dist wardha": "Wardha", "sevagram": "Wardha", "sindhi": "Wardha",
        "sindhi(meghe)": "Wardha", "hinganghat": "Wardha",

        # Bhandara
        "bhandara": "Bhandara", "sakoli": "Bhandara", "tumsar": "Bhandara",

        # Gondia
        "gondia": "Gondia", "tirora": "Gondia",

        # Jalna
        "jalna": "Jalna", "bhokardan": "Jalna", "partur": "Jalna",

        # Parbhani
        "parbhani": "Parbhani", "gangakhed": "Parbhani", "jintur": "Parbhani",

        # Washim
        "washim": "Washim", "karanja": "Washim", "risod": "Washim",

        # Hingoli
        "hingoli": "Hingoli", "basmath": "Hingoli",

        # Ratnagiri
        "ratnagiri": "Ratnagiri", "ratnagiri.": "Ratnagiri",
        "rajendra mane college of engineering & technology ambav deorukh": "Ratnagiri", "chiplun": "Ratnagiri",

        # Sindhudurg
        "sindhudurg": "Sindhudurg", "sindhudurg.": "Sindhudurg", "kankavli": "Sindhudurg",
        "sawantwadi": "Sindhudurg", "malvan": "Sindhudurg",

        # Gadchiroli
        "gadchiroli": "Gadchiroli", "aheri": "Gadchiroli", "armori": "Gadchiroli",
    }
)


def _clean_and_resolve_city(
    raw_city: Any,
    college_name: Any = "",
    college_code: Any = "",
    code_city_map: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Strictly cleans, normalizes, and maps city strings to standard Maharashtra districts."""
    s = str(raw_city or "").strip().lower()

    # 1. Remove 6-digit Pincodes
    if re.search(r"\b\d{6}\b", s):
        s = re.sub(r"\b\d{6}\b", "", s).strip()

    # 2. Check direct town match
    if s in _TOWN_TO_DISTRICT_MAP:
        return _TOWN_TO_DISTRICT_MAP[s]

    # 3. Direct district match
    if s in _DISTRICT_LOWER_MAP:
        return _DISTRICT_LOWER_MAP[s]

    # 4. Search for district keyword in city string
    for dist in MAHARASHTRA_DISTRICTS:
        if re.search(r"\b" + re.escape(dist.lower()) + r"\b", s):
            return dist

    # 5. Search for town keyword in city string
    for town, dist in _TOWN_TO_DISTRICT_MAP.items():
        if len(town) >= 4 and re.search(r"\b" + re.escape(town) + r"\b", s):
            return dist

    # 6. Fallback to institute metadata via DTE code
    if code_city_map and college_code:
        zcode = str(college_code).strip().zfill(5)
        inst_city = code_city_map.get(zcode)
        if inst_city and inst_city in _MAHA_DISTRICT_SET:
            return "Dharashiv" if inst_city == "Osmanabad" else inst_city

    # 7. Fallback: search college name for district or town
    cname = str(college_name or "").lower()
    for dist in MAHARASHTRA_DISTRICTS:
        if re.search(r"\b" + re.escape(dist.lower()) + r"\b", cname):
            return dist

    for town, dist in _TOWN_TO_DISTRICT_MAP.items():
        if len(town) >= 4 and re.search(r"\b" + re.escape(town) + r"\b", cname):
            return dist

    return None


# ── 2. BRANCH NOISE CLEANING & CANONICAL MAPPING ──────────────────────────────
_NOISE_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b5G\b", re.I),
    re.compile(r"\b\d{6}\b"),
    re.compile(r"\b(?:1st|2nd)\s+Shift\b", re.I),
    re.compile(r"\bShift-I+\b", re.I),
    re.compile(r"\bAgaskhind\s+Tal\.?\b", re.I),
    re.compile(r"\bSS\b"),
    re.compile(r"\bFW\b"),
)

CANONICAL_BRANCH_MAP = {
    # ── 1. MAP STANDALONE AI/ML/DS/CYBER UNDER THE "COMPUTER SCIENCE" UMBRELLA ──
    "artificial intelligence": "Computer Science and Engineering (Artificial Intelligence)",
    "artificial intelligence (ai) and data science": "Computer Science and Engineering (Artificial Intelligence and Data Science)",
    "artificial intelligence and data science": "Computer Science and Engineering (Artificial Intelligence and Data Science)",
    "artificial intelligence and machine learning": "Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
    "cyber security": "Computer Science and Engineering (Cyber Security)",
    "data engineering": "Computer Science and Engineering (Data Science)",
    "data science": "Computer Science and Engineering (Data Science)",
    "internet of things": "Computer Science and Engineering (IoT)",
    "industrial iot": "Computer Science and Engineering (IoT)",

    # ── 2. STANDARDIZE EXISTING CS SPECIALISATION NAMES (clean brackets/typos) ──
    "computer science and engineering (ai & data science)": "Computer Science and Engineering (Artificial Intelligence and Data Science)",
    "computer science and engineering (ai & machine learning)": "Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
    "computer science and engineering (ai)": "Computer Science and Engineering (Artificial Intelligence)",
    "computer science and engineering (data science)": "Computer Science and Engineering (Data Science)",
    "computer science and engineering (cyber security)": "Computer Science and Engineering (Cyber Security)",
    "computer science and engineering (iot, cyber security & blockchain)": "Computer Science and Engineering (IoT, Cyber Security & Blockchain)",
    "computer science and engineering (internet of things and cyber security including block chain)": "Computer Science and Engineering (IoT, Cyber Security & Blockchain)",
    "computer science and engineering (internet of things and cyber security including block chain technology)": "Computer Science and Engineering (IoT, Cyber Security & Blockchain)",
    "computer science and engineering (artificial intelligence and data science)": "Computer Science and Engineering (Artificial Intelligence and Data Science)",
    "computer science and engineering (artificial intelligence and machine learning)": "Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
    "computer science and engineering(artificial intelligence and machine learning)": "Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
    "computer science and engineering(data science)": "Computer Science and Engineering (Data Science)",
    "computer science and engineering(cyber security)": "Computer Science and Engineering (Cyber Security)",

    # ── 3. CLEAN UP REDUNDANT RAW CS & IT OVERLAPS ───────────────────────────────
    "computer science and technology": "Computer Science and Engineering",
    "computer science and design": "Computer Science and Engineering",
    "computer science and business systems": "Computer Science and Engineering",
    "computer science and information technology": "Computer Science and Engineering",
    "computer science": "Computer Science and Engineering",
    "computer technology": "Computer Engineering",
    "computer engineering (software engineering)": "Computer Engineering",
    "computer engineering (regional language)": "Computer Engineering",

    # ── 4. CIVIL CLEANUP ──────────────────────────────────────────────────────────
    "civil engineering and planning": "Civil Engineering",
    "civil and environmental engineering": "Civil Engineering",
    "civil and infrastructure engineering": "Civil Engineering",
    "civil engineering (structural engineering)": "Civil Engineering",
    "civil engineering with computer application": "Civil Engineering",

    # ── 5. ELECTRICAL & ELECTRONICS CLEANUP ──────────────────────────────────────
    "electrical engg": "Electrical Engineering",
    "electrical, electronics and power": "Electrical and Electronics Engineering",
    "electronics engineering ( vlsi design and technology)": "Electronics Engineering (VLSI Design and Technology)",
    "electronics engineering (vlsi design and technology)": "Electronics Engineering (VLSI Design and Technology)",
    "vlsi": "Electronics Engineering (VLSI Design and Technology)",
    "vlsi design": "Electronics Engineering (VLSI Design and Technology)",
    "electronics and communication (advanced communication technology)": "Electronics and Communication Engineering",
    "electronics and computer science": "Electronics and Computer Engineering",
    "electronics and telecommunication engg": "Electronics and Telecommunication Engineering",
    "electronics & telecommunication engineering": "Electronics and Telecommunication Engineering",

    # ── 6. ROBOTICS (not CS) ──────────────────────────────────────────────────────
    "automation and robotics": "Robotics and Automation",
    "robotics and artificial intelligence": "Robotics and Automation",

    # ── 7. MECHANICAL CLEANUP ─────────────────────────────────────────────────────
    "mechanical & automation engineering": "Mechanical Engineering",
    "mechanical and mechatronics engineering (additive manufacturing)": "Mechanical Engineering",
    "mechanical engineering[sandwich]": "Mechanical Engineering",
    "production engineering[sandwich]": "Production Engineering",
}

_ACRONYM_MAP: Mapping[str, str] = MappingProxyType(
    {
        "iot": "IoT",
        "vlsi": "VLSI",
        "ai": "AI",
        "ml": "ML",
        "ds": "DS",
        "cse": "CSE",
        "it": "IT",
        "extc": "EXTC",
    }
)

_LOWERCASE_WORDS: frozenset[str] = frozenset(
    {"and", "in", "of", "for", "with", "to", "at", "by", "on", "the"}
)


def _clean_and_normalize_branch(raw_branch: Any) -> Optional[str]:
    """Clean bracketed noise, encoding artifacts, fix casing/acronyms, and apply canonical mapping."""
    if raw_branch is None or (isinstance(raw_branch, float) and pd.isna(raw_branch)):
        return None
    val = str(raw_branch).strip()
    if not val or val.lower() in {"nan", "none", "null"}:
        return None

    # 1. Strip non-ASCII/encoding garbage characters like Â and hidden control bytes
    cleaned = re.sub(r"[^\x20-\x7E]", "", val)

    # 2. Strip all bracketed strings: e.g. [Sandwich], [Direct Second Year Second Shift]
    cleaned = re.sub(r"\[.*?\]", "", cleaned)

    # 3. Strip noise tokens (e.g. 5G, pincodes, Shift-I, etc.)
    for pattern in _NOISE_TOKEN_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    # 4. Fix broken parentheses: strip unmatched leading/trailing parens
    cleaned = cleaned.strip()
    if cleaned.endswith(")") and "(" not in cleaned:
        cleaned = cleaned[:-1]
    if cleaned.startswith("(") and ")" not in cleaned:
        cleaned = cleaned[1:]

    # 5. Fix comma spacing
    cleaned = re.sub(r",([^\s])", r", \1", cleaned)

    # 6. Standardize slashes: ensure proper spacing around slashes
    cleaned = re.sub(r"\s*/\s*", " / ", cleaned)

    # Ensure missing spaces before parentheses are fixed globally
    cleaned = re.sub(r"(?<=[a-zA-Z])\(", " (", cleaned)

    # Normalize intermediate whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;-")
    if not cleaned:
        return None

    # 7. Canonical Deduplication Mapping Matrix
    lookup_clean = cleaned.casefold()
    lookup_raw = val.casefold()
    if lookup_clean in CANONICAL_BRANCH_MAP:
        return CANONICAL_BRANCH_MAP[lookup_clean]
    if lookup_raw in CANONICAL_BRANCH_MAP:
        return CANONICAL_BRANCH_MAP[lookup_raw]

    raw_no_brackets = re.sub(r"\[.*?\]", "", val).strip().casefold()
    if raw_no_brackets in CANONICAL_BRANCH_MAP:
        return CANONICAL_BRANCH_MAP[raw_no_brackets]

    # Handle unclosed paren in block chain entry
    if "(" in cleaned and not cleaned.endswith(")"):
        closed = (cleaned + ")").casefold()
        if closed in CANONICAL_BRANCH_MAP:
            return CANONICAL_BRANCH_MAP[closed]

    # 8. Casing & Acronym Preservation
    letters_only = re.sub(r"[^a-zA-Z]", "", cleaned)
    if letters_only and letters_only.isupper():
        def _replace_token(match: re.Match[str]) -> str:
            w = match.group(0)
            wl = w.lower()
            if wl in _ACRONYM_MAP:
                return _ACRONYM_MAP[wl]
            if wl in _LOWERCASE_WORDS:
                return wl
            return w.capitalize()

        cleaned = re.sub(r"[A-Za-z0-9]+", _replace_token, cleaned)
        cleaned = re.sub(r"(^|[\(\[\/]\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), cleaned)
    else:
        def _preserve_acronym(match: re.Match[str]) -> str:
            w = match.group(0)
            wl = w.lower()
            return _ACRONYM_MAP.get(wl, w)

        cleaned = re.sub(r"\b[A-Za-z0-9]+\b", _preserve_acronym, cleaned)

    return cleaned or None


_normalize_branch = _clean_and_normalize_branch


def _resolve_data_file(data_dir: Path, filename: str) -> Optional[Path]:
    """Resolve a manifest filename against the data directory, case-insensitively."""
    aliases = FILE_ALIASES.get(filename.casefold(), (filename,))
    names = []
    for alias in (filename, *aliases):
        if alias.casefold() not in {n.casefold() for n in names}:
            names.append(alias)

    if not data_dir.is_dir():
        return None

    index = {p.name.casefold(): p for p in data_dir.iterdir() if p.is_file()}
    for name in names:
        hit = index.get(name.casefold())
        if hit is not None:
            return hit
    return None


def _manifest_for_path(source_path: Path) -> Optional[Mapping[str, Any]]:
    source_name = source_path.name.casefold()
    if source_name in MANIFEST:
        return MANIFEST[source_name]
    for key, meta in MANIFEST.items():
        aliases = {key.casefold(), *(a.casefold() for a in FILE_ALIASES.get(key.casefold(), ()))}
        if source_name in aliases:
            return meta
    return None


# ── 3. COLLEGE TYPE & CATEGORY SANITATION ─────────────────────────────────────
ALLOWED_COLLEGE_TYPES: list[str] = [
    "Government",
    "Government Aided",
    "Autonomous",
    "Un-Aided Autonomous",
    "Un-Aided Non-Autonomous",
]

_CATEGORY_MAP: Mapping[str, str] = MappingProxyType(
    {
        "OPEN": "OPEN", "GOPEN": "OPEN", "GOPENS": "OPEN", "LOPEN": "OPEN", "LOPENS": "OPEN",
        "GOPENH": "OPEN", "GOPENO": "OPEN", "LOPENH": "OPEN", "LOPENO": "OPEN", "AI": "OPEN",
        "OBC": "OBC", "GOBC": "OBC", "GOBCS": "OBC", "LOBC": "OBC", "LOBCS": "OBC", "GOBCH": "OBC", "GOBCO": "OBC",
        "SC": "SC", "GSC": "SC", "GSCS": "SC", "LSC": "SC", "LSCS": "SC", "GSCH": "SC", "GSCO": "SC",
        "ST": "ST", "GST": "ST", "GSTS": "ST", "LST": "ST", "LSTS": "ST", "GSTH": "ST", "GSTO": "ST",
        "EWS": "EWS", "GEWS": "EWS", "LEWS": "EWS",
        "SEBC": "SEBC", "GSEBC": "SEBC", "GSEBCS": "SEBC", "LSEBC": "SEBC", "LSEBCS": "SEBC",
        "TFWS": "TFWS",
        "VJ": "VJ", "VJA": "VJ", "GVJ": "VJ", "LVJ": "VJ",
        "NT-1": "NT-1", "NTA": "NT-1", "NT-A": "NT-1", "GNT1": "NT-1", "LNT1": "NT-1", "NT1": "NT-1",
        "NT-2": "NT-2", "NTB": "NT-2", "NT-B": "NT-2", "GNT2": "NT-2", "LNT2": "NT-2", "NT2": "NT-2",
        "NT-3": "NT-3", "NTC": "NT-3", "NT-C": "NT-3", "NTD": "NT-3", "NT-D": "NT-3", "NT3": "NT-3",
        "DEF-OPEN": "DEF-OPEN", "DEF-OBC": "DEF-OBC", "DEF-SC": "DEF-SC", "DEF-ST": "DEF-ST",
        "PWDOPE": "PWDOPE", "PWD": "PWDOPE",
    }
)


def _standardize_college_type(raw_val: Any) -> str:
    if not raw_val or pd.isna(raw_val):
        return "Un-Aided Non-Autonomous"
    s_lower = str(raw_val).strip().lower()

    is_govt = "government" in s_lower or "govt" in s_lower
    is_aided = "aided" in s_lower and "un-aided" not in s_lower and "unaided" not in s_lower
    is_autonomous = "autonomous" in s_lower and "non-autonomous" not in s_lower and "non autonomous" not in s_lower
    is_unaided = "un-aided" in s_lower or "unaided" in s_lower or "private" in s_lower

    if is_govt and is_aided:
        return "Government Aided"
    if is_unaided and is_autonomous:
        return "Un-Aided Autonomous"
    if is_unaided or ("non-autonomous" in s_lower):
        return "Un-Aided Non-Autonomous"
    if is_govt and not is_aided:
        return "Government"
    if is_autonomous:
        return "Autonomous"
    return "Un-Aided Non-Autonomous"


def _standardize_seat_type(seat_val: Any, cat_val: Any = "") -> str:
    target = str(seat_val or cat_val or "").strip().upper()
    if "TFWS" in target:
        return "TFWS"
    if "EWS" in target:
        return "EWS"
    if "OPEN" in target:
        if target.endswith("O") or "OHU" in target or "OTHER" in target:
            return "GOPENO"
        return "GOPENH"
    if "OBC" in target:
        return "GOBC"
    if "SC" in target:
        return "GSC"
    if "ST" in target:
        return "GST"
    if "SEBC" in target:
        return "GSEBC"
    return target if target else "GOPENH"


def _normalise_dte_codes(values: pd.Series, names: pd.Series) -> pd.Series:
    source_codes = values.astype("string").str.extract(r"(\d{1,5})", expand=False)
    embedded_codes = names.astype("string").str.extract(r"(\d{1,5})", expand=False)
    selected = source_codes.fillna(embedded_codes)
    numeric_codes = pd.to_numeric(selected, errors="coerce").astype("Int64")
    return numeric_codes.astype("string").str.zfill(5)


# ── DataLoader Engine ─────────────────────────────────────────────────────────
class DataLoader:
    """Loads canonical, validated admissions fact tables with strict ETL sanitation."""

    COL_COLLEGE_NAME = COL_COLLEGE_NAME
    COL_COLLEGE_CODE = COL_COLLEGE_CODE
    COL_CITY = COL_CITY
    COL_BRANCH = COL_BRANCH
    COL_CATEGORY = COL_CATEGORY
    COL_CUTOFF = COL_CUTOFF
    COL_YEAR = COL_YEAR
    COL_TYPE = COL_TYPE
    COL_SEAT_CODE = COL_SEAT_CODE
    COL_ROUND = COL_ROUND
    HINTS = _COLUMN_ALIASES

    def __init__(self, data_dir: Optional[str | Path] = None, institutes_json: Optional[str | Path] = None) -> None:
        base = Path(__file__).resolve().parent
        self.data_dir = Path(data_dir) if data_dir else base / "data"

        if institutes_json:
            self.institutes_json = Path(institutes_json)
        elif (self.data_dir / "institutes.json").is_file():
            self.institutes_json = self.data_dir / "institutes.json"
        else:
            self.institutes_json = base / "institutes.json"

        self.city_map_path = self.data_dir / "college_city_map.json"
        self.institutes_data: Dict[str, Any] = {}
        self._college_city_by_code: Dict[str, str] = {}
        self._college_type_by_code: Dict[str, str] = {}
        self._college_city_map: Dict[str, str] = {}
        self._cached_df: Optional[pd.DataFrame] = None
        self._cached_filters: Optional[Dict[str, Any]] = None
        self._cache_signature: Optional[tuple[tuple[str, Optional[int]], ...]] = None

        self._load_institutes()
        self._load_city_map()

    def _load_institutes(self) -> None:
        if not self.institutes_json.is_file():
            log.warning("Institute metadata file missing: %s", self.institutes_json)
            return
        try:
            with self.institutes_json.open("r", encoding="utf-8") as file_handle:
                raw = json.load(file_handle)
            self.institutes_data = raw if isinstance(raw, dict) else {}

            for code, record in self.institutes_data.items():
                if not isinstance(record, dict):
                    continue
                zcode = str(code).strip().zfill(5)

                raw_city = record.get("city") or record.get("location", "").split(",")[0].strip()
                resolved_city = _clean_and_resolve_city(raw_city, record.get("name", ""), zcode)
                if resolved_city:
                    self._college_city_by_code[zcode] = resolved_city

                status = record.get("status") or (record.get("system_overview", {}).get("status") if isinstance(record.get("system_overview"), dict) else "") or "Un-Aided"
                autonomy = record.get("autonomy") or (record.get("system_overview", {}).get("autonomy") if isinstance(record.get("system_overview"), dict) else "") or "Non-Autonomous"
                self._college_type_by_code[zcode] = _standardize_college_type(f"{status} {autonomy}")

        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.error("Could not load institute metadata: %s", exc)
            self.institutes_data = {}

    def _load_city_map(self) -> None:
        if not self.city_map_path.is_file():
            return
        try:
            with self.city_map_path.open("r", encoding="utf-8") as file_handle:
                raw = json.load(file_handle)
            self._college_city_map = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not load college city map: %s", exc)
            self._college_city_map = {}

    def _discover_prediction_csvs(self, pathway: Optional[str] = None) -> List[Path]:
        selected_manifest = MANIFEST
        if pathway:
            p = str(pathway).strip().lower()
            if p in ("diploma",):
                p = "dse"
            selected_manifest = {k: v for k, v in MANIFEST.items() if v.get("pathway") == p}

        resolved: List[Path] = []
        seen: set[str] = set()
        for filename in selected_manifest:
            path = _resolve_data_file(self.data_dir, filename)
            if path is None:
                log.warning("Pathway dataset missing: %s (pathway=%s)", filename, pathway or "all")
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            resolved.append(path)
        return resolved

    def _source_signature(self) -> tuple[tuple[str, Optional[int]], ...]:
        tracked_paths = [self.data_dir / filename for filename in MANIFEST]
        tracked_paths.extend([self.institutes_json, self.city_map_path])
        return tuple(sorted((str(path), path.stat().st_mtime_ns if path.exists() else None) for path in tracked_paths))

    def _is_cache_valid(self) -> bool:
        return self._cached_df is not None and self._cache_signature == self._source_signature()

    def map_columns(self, frame: pd.DataFrame) -> pd.DataFrame:
        lookup = {re.sub(r"\s+", " ", str(column).strip().casefold()): column for column in frame.columns}
        rename_map: Dict[str, str] = {}
        for canonical, aliases in _COLUMN_ALIASES.items():
            source = next((lookup[alias] for alias in aliases if alias in lookup), None)
            if source is not None:
                rename_map[source] = canonical
        return frame.rename(columns=rename_map)

    def load_dataset(self, path: str | Path) -> pd.DataFrame:
        """ETL pipeline: loads, cleans, normalizes, and sanitizes a single admissions dataset."""
        source_path = Path(path)
        source_name = source_path.name.casefold()
        metadata = _manifest_for_path(source_path)
        if metadata is None:
            if source_name in REJECTED_DATASETS:
                log.info("Skipped non-manifest admissions dataset: %s", source_path.name)
            return pd.DataFrame()

        try:
            frame = pd.read_csv(source_path, dtype=str, low_memory=False)
        except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            log.error("Unable to read %s: %s", source_path.name, exc)
            return pd.DataFrame()

        frame = self.map_columns(frame)

        # Dynamic Cutoff Column Resolution
        if COL_CUTOFF not in frame.columns:
            for candidate in ["percentage", "percent", "cutoff", "score", "cutoff_value", "cutoff rank"]:
                matching_col = next((c for c in frame.columns if candidate in c.lower()), None)
                if matching_col:
                    frame[COL_CUTOFF] = frame[matching_col]
                    break

        # Resilient fallback: a missing/unmapped category column should not
        # drop the whole file. Default absent category data to "OPEN" rather
        # than failing the required-column check below.
        if COL_CATEGORY not in frame.columns:
            log.info(
                "No category-like column found in %s; defaulting %s to 'OPEN'.",
                source_path.name, COL_CATEGORY,
            )
            frame[COL_CATEGORY] = "OPEN"

        required = {COL_COLLEGE_NAME, COL_BRANCH, COL_CUTOFF}
        missing = required.difference(frame.columns)
        if missing:
            log.warning("Skipped %s; required columns missing: %s", source_path.name, sorted(missing))
            return pd.DataFrame()

        college_name = frame[COL_COLLEGE_NAME].astype("string").str.strip()
        source_codes = frame[COL_COLLEGE_CODE] if COL_COLLEGE_CODE in frame.columns else pd.Series(pd.NA, index=frame.index, dtype="string")
        college_code = _normalise_dte_codes(source_codes, college_name).astype("string").str.strip().str.zfill(5)

        # ── 1. City / District Sanitation ─────────────────────────────────────
        city_source = frame[COL_CITY] if COL_CITY in frame.columns else pd.Series("", index=frame.index)
        cleaned_cities = [
            _clean_and_resolve_city(
                raw_city=c_val,
                college_name=name_val,
                college_code=code_val,
                code_city_map=self._college_city_by_code,
            )
            for c_val, name_val, code_val in zip(city_source, college_name, college_code)
        ]
        city_series = pd.Series(cleaned_cities, index=frame.index, dtype="string")

        # ── 2. Branch noise cleaning only (AIML/AIDS/CSE/IT/EXTC preserved) ──
        branch_source = frame[COL_BRANCH]
        cleaned_branches = [_clean_and_normalize_branch(b) for b in branch_source]
        branch_series = pd.Series(cleaned_branches, index=frame.index, dtype="string")

        # ── 3. Preserve the CAP seat code exactly as published. ──────────────
        # The Category column in the source files contains CAP seat codes (such
        # as GOPENH and LOPENH), not a student's declared reservation category.
        # Keep that fact intact for exact filtering and display.
        raw_cat = frame[COL_CATEGORY].astype("string").str.strip().str.upper()
        raw_cat = raw_cat.fillna("").mask(raw_cat.str.len() == 0, "UNKNOWN")
        # category remains a family for the candidate-category filter only;
        # seat_code is the unmodified CSV value used for exact matching.
        clean_cat = raw_cat.map(_CATEGORY_MAP).fillna(raw_cat)
        seat_series = raw_cat.astype("string")
        round_source = frame[COL_ROUND].astype("string").str.strip() if COL_ROUND in frame.columns else pd.Series(pd.NA, index=frame.index, dtype="string")

        quota_source = frame[COL_QUOTA].astype("string").str.strip().str.upper() if COL_QUOTA in frame.columns else pd.Series("STATE", index=frame.index, dtype="string")

        # ── 4. Score & Rank Resolution ────────────────────────────────────────
        cutoff_numeric = pd.to_numeric(frame[COL_CUTOFF].astype(str).str.replace("%", "", regex=False), errors="coerce").astype("float64")
        valid_cutoff = cutoff_numeric.notna() & cutoff_numeric.between(0.0, 100.0, inclusive="both")
        state_rank = pd.to_numeric(frame[COL_STATE_RANK], errors="coerce").astype("Int64") if COL_STATE_RANK in frame.columns else pd.Series(pd.NA, index=frame.index, dtype="Int64")

        # ── 5. College Type Standardization ───────────────────────────────────
        type_from_code = college_code.map(self._college_type_by_code).fillna("Un-Aided Non-Autonomous")

        normalized = pd.DataFrame(
            {
                COL_COLLEGE_CODE: college_code,
                COL_COLLEGE_NAME: college_name,
                COL_CITY: city_series,
                COL_BRANCH: branch_series,
                COL_CATEGORY: clean_cat,
                COL_QUOTA: quota_source,
                COL_SEAT_TYPE: seat_series,
                COL_SEAT_CODE: seat_series,
                COL_ROUND: round_source,
                COL_CUTOFF: cutoff_numeric,
                COL_STATE_RANK: state_rank,
                COL_EXAM_TYPE: metadata["exam_type"],
                COL_YEAR: int(metadata["year"]),
                COL_TYPE: type_from_code,
            }
        )

        # Retention
        valid_city_mask = normalized[COL_CITY].isin(_MAHA_DISTRICT_SET)
        valid_branch_mask = normalized[COL_BRANCH].notna() & (normalized[COL_BRANCH].str.len() > 2)
        valid_rows = valid_cutoff & valid_city_mask & valid_branch_mask & normalized[[COL_COLLEGE_CODE, COL_COLLEGE_NAME]].notna().all(axis=1)

        cleaned_df = normalized.loc[valid_rows].copy()
        log.info(
            "ETL Pipeline: Ingested %d clean records (retained %d of %d) from %s",
            len(cleaned_df), len(cleaned_df), len(frame), source_path.name
        )
        return cleaned_df

    def get_all_data(self) -> pd.DataFrame:
        """Hydrates or retrieves cached sanitized admissions dataset."""
        if self._is_cache_valid():
            return self._cached_df

        frames = [self.load_dataset(path) for path in self._discover_prediction_csvs()]
        usable = [f for f in frames if not f.empty]
        if not usable:
            self._cached_df = pd.DataFrame(
                columns=[
                    COL_COLLEGE_CODE, COL_COLLEGE_NAME, COL_CITY, COL_BRANCH,
                    COL_CATEGORY, COL_QUOTA, COL_SEAT_TYPE, COL_CUTOFF,
                    COL_STATE_RANK, COL_EXAM_TYPE, COL_YEAR, COL_TYPE,
                ]
            )
        else:
            combined = pd.concat(usable, ignore_index=True)
            # A CAP record is meaningful at College + Branch + Seat Code +
            # Round + Year granularity.  Do not reduce distinct CSV records.
            grain = [COL_COLLEGE_CODE, COL_BRANCH, COL_SEAT_CODE, COL_ROUND, COL_EXAM_TYPE, COL_YEAR, COL_CUTOFF]
            self._cached_df = combined.sort_values(grain, kind="stable").reset_index(drop=True)

        self._cached_filters = None
        self._cache_signature = self._source_signature()
        log.info("DataLoader cache hydrated with %d pristine admissions rows.", len(self._cached_df))
        return self._cached_df

    def get_combined_cet_data(self) -> pd.DataFrame:
        return self.get_all_data().loc[lambda f: f[COL_EXAM_TYPE].eq("CET")].copy()

    def get_combined_diploma_data(self) -> pd.DataFrame:
        return self.get_all_data().loc[lambda f: f[COL_EXAM_TYPE].isin(["DIPLOMA", "DSE"])].copy()

    def get_data_by_pathway(self, pathway: str = "fe") -> pd.DataFrame:
        clean = str(pathway or "fe").strip().lower()
        if clean in ("dse", "diploma"):
            return self.get_combined_diploma_data()
        return self.get_combined_cet_data()

    def get_branches(self, pathway: Optional[str] = None) -> list[str]:
        """Returns 100% unique, non-empty, clean engineering discipline branches sorted alphabetically."""
        data = self.get_data_by_pathway(pathway) if pathway else self.get_all_data()
        raw_branches = data[COL_BRANCH].dropna().tolist() if COL_BRANCH in data.columns else []
        cleaned_branches: set[str] = set()
        for b in raw_branches:
            norm = _clean_and_normalize_branch(b)
            if norm and norm.strip():
                cleaned_branches.add(norm.strip())
        return sorted(list(cleaned_branches))

    def get_unique_filters(self, pathway: Optional[str] = None) -> Dict[str, Any]:
        """Provides pristine, deduplicated, sorted dropdown values."""
        data = self.get_data_by_pathway(pathway) if pathway else self.get_all_data()

        present_cities = set(data[COL_CITY].dropna().unique().tolist())
        cities = [d for d in MAHARASHTRA_DISTRICTS if d in present_cities] or list(MAHARASHTRA_DISTRICTS)

        branches = self.get_branches(pathway=pathway)

        college_types = ALLOWED_COLLEGE_TYPES[:]
        extra_types = sorted(t for t in data[COL_TYPE].dropna().unique().tolist() if t not in college_types)
        college_types.extend(extra_types)

        return {
            "cities": cities,
            "branches": branches,
            "categories": sorted(data[COL_CATEGORY].dropna().unique().tolist()),
            "seat_codes": sorted(data[COL_SEAT_CODE].dropna().unique().tolist()),
            "years": sorted(data[COL_YEAR].dropna().unique().tolist(), reverse=True),
            "rounds": sorted(data[COL_ROUND].dropna().unique().tolist()),
            "college_types": college_types,
            "advanced": {},
        }

    def get_filter_metadata(self, pathway: Optional[str] = None) -> Dict[str, Any]:
        return self.get_unique_filters(pathway)

    def get_college_type(self, college_code: str) -> str:
        zcode = str(college_code).strip().zfill(5)
        return self._college_type_by_code.get(zcode, "Un-Aided Non-Autonomous")

    def get_job_data(self) -> pd.DataFrame:
        paths = [self.data_dir / "JobsDatasetProcessed.csv", self.data_dir / "data.csv"]
        frames = []
        for p in paths:
            if p.is_file():
                try:
                    frames.append(pd.read_csv(p, low_memory=False))
                except Exception as e:
                    log.warning("Could not load job dataset %s: %s", p.name, e)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def get_candidate_job_data(self) -> pd.DataFrame:
        path = self.data_dir / "candidate_job_role_dataset.csv"
        return pd.read_csv(path, low_memory=False) if path.is_file() else pd.DataFrame()

    def get_resume_dataset(self) -> pd.DataFrame:
        path = self.data_dir / "Resume.csv"
        return pd.read_csv(path, low_memory=False) if path.is_file() else pd.DataFrame()

    def get_placement_data(self) -> Dict[str, Any]:
        return self.institutes_data

    def verify(self) -> Dict[str, Any]:
        data = self.get_all_data()
        filters = self.get_unique_filters()
        return {
            "total_rows": int(len(data)),
            "total_colleges": int(data[COL_COLLEGE_CODE].nunique()),
            "total_branches": len(filters["branches"]),
            "total_cities": len(filters["cities"]),
            "total_categories": len(filters["categories"]),
            "total_college_types": len(filters["college_types"]),
            "cities": filters["cities"],
            "branches": filters["branches"],
            "college_types": filters["college_types"],
        }
