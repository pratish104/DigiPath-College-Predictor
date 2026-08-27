"""
DigiPath — Production Data Loader (data_loader.py)
====================================================
Principal Engineer Refactor: Single source of truth for all datasets.

Architecture:
    CSV -> Read -> Column Mapping -> Validation -> Cleaning
        -> Normalization -> Deduplication -> Metadata Enrichment
        -> Caching -> Return DataFrame

Public API (unchanged):
    DataLoader(data_dir, institutes_json)
    .load_dataset(path)
    .get_all_data()
    .get_combined_cet_data()
    .get_combined_diploma_data()
    .get_unique_filters()
    .get_job_data()
    .get_candidate_job_data()
    .get_resume_dataset()
    .get_placement_data()
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Set

import pandas as pd

log = logging.getLogger("digipath.data_loader")

# ---------------------------------------------------------------------------
# CONSTANTS -- Standard Internal Column Names
# ---------------------------------------------------------------------------

COL_COLLEGE_NAME = "college_name"
COL_COLLEGE_CODE = "college_code"
COL_CITY         = "city"
COL_BRANCH       = "branch"
COL_CATEGORY     = "category"
COL_CUTOFF       = "cutoff_value"
COL_YEAR         = "year"
COL_TYPE         = "college_type"

# ---------------------------------------------------------------------------
# CONSTANTS -- Fuzzy Column Mapping Hints
# ---------------------------------------------------------------------------

_COLUMN_HINTS: Dict[str, List[str]] = {
    COL_COLLEGE_NAME: [
        "college name", "institute name", "institute", "college_name",
        "college", "school name",
    ],
    COL_COLLEGE_CODE: [
        "college code", "dte code", "college_code", "dte", "inst code",
        "institution code",
    ],
    COL_CITY: [
        "city", "location", "district", "region", "place", "town",
    ],
    COL_BRANCH: [
        "branch", "course", "course name", "stream", "programme",
        "program", "subject", "specialization", "discipline",
    ],
    COL_CATEGORY: [
        "category", "seat type", "caste", "reservation", "gender", "quota",
    ],
    COL_CUTOFF: [
        "cutoff", "cut off", "percent", "percentage", "percentile",
        "score", "marks", "rank",
    ],
    COL_YEAR: [
        "year", "session", "academic year", "batch",
    ],
}

# ---------------------------------------------------------------------------
# CONSTANTS -- Valid Admission Categories
# Includes canonical forms AND common dataset variants (G/L prefix, S suffix)
# ---------------------------------------------------------------------------

_CATEGORY_MAP: Dict[str, str] = {
    # OPEN / GENERAL
    "OPEN": "OPEN",    "GOPEN": "OPEN",   "LOPEN": "OPEN",
    "GOPENS": "OPEN",  "LOPENS": "OPEN",  "OPENS": "OPEN",
    "GEN": "OPEN",     "GENERAL": "OPEN",

    # OBC
    "OBC": "OBC",   "GOBC": "OBC",  "LOBC": "OBC",
    "GOBCS": "OBC", "LOBCS": "OBC",

    # SC
    "SC": "SC",   "GSC": "SC",  "LSC": "SC",
    "GSCS": "SC", "LSCS": "SC",

    # ST
    "ST": "ST",   "GST": "ST",  "LST": "ST",
    "GSTS": "ST", "LSTS": "ST",

    # EWS
    "EWS": "EWS",    "GEWS": "EWS",  "LEWS": "EWS",
    "GEWSA": "EWS",  "GEWSO": "EWS",

    # SEBC
    "SEBC": "SEBC", "GSEBC": "SEBC", "LSEBC": "SEBC",

    # VJ / DT
    "VJ": "VJ",  "GVJ": "VJ", "LVJ": "VJ",
    "VJA": "VJ", "DT": "VJ",

    # NT-A
    "NT-A": "NT-A", "NTA": "NT-A",  "NT1": "NT-A",
    "GNT1": "NT-A", "LNT1": "NT-A", "NT-1": "NT-A",

    # NT-B
    "NT-B": "NT-B", "NTB": "NT-B",  "NT2": "NT-B",
    "GNT2": "NT-B", "LNT2": "NT-B", "NT-2": "NT-B",

    # NT-C
    "NT-C": "NT-C", "NTC": "NT-C",  "NT3": "NT-C",
    "GNT3": "NT-C", "LNT3": "NT-C", "NT-3": "NT-C",

    # NT-D
    "NT-D": "NT-D", "NTD": "NT-D",  "NT4": "NT-D",
    "GNT4": "NT-D", "LNT4": "NT-D", "NT-4": "NT-D",

    # PWD
    "PWD": "PWD", "PH": "PWD", "HANDICAPPED": "PWD",

    # TFWS
    "TFWS": "TFWS", "TFW": "TFWS",

    # OCI / CIWGC
    "OCI": "OCI",
    "CIWGC": "CIWGC",

    # DEF / Defence (kept raw -- predictors depend on these codes)
    "DEF-O": "DEF-O",   "DEFOBC": "DEFOBC", "DEFOPE": "DEFOPE",
    "DEFSC": "DEFSC",   "DEFST": "DEFST",
}

_INVALID_CATEGORY_RE = re.compile(
    r"(cut\s*off|general\s*merit|merit\s*list|stage|note|footnote|"
    r"indicates|seat\s*matrix|admission|figure|bracket|total|rank\s*list)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# CONSTANTS -- Canonical Maharashtra Cities
# ---------------------------------------------------------------------------

_MAHARASHTRA_CITIES: List[str] = [
    "Mumbai", "Pune", "Nagpur", "Thane", "Nashik", "Aurangabad",
    "Chhatrapati Sambhajinagar", "Navi Mumbai", "Solapur", "Amravati",
    "Nanded", "Kolhapur", "Ulhasnagar", "Sangli", "Malegaon", "Akola",
    "Latur", "Dhule", "Ahmednagar", "Chandrapur", "Parbhani",
    "Ichalkaranji", "Jalna", "Ambarnath", "Bhusawal", "Panvel",
    "Badlapur", "Beed", "Gondia", "Satara", "Barshi", "Yavatmal",
    "Dharashiv", "Osmanabad", "Nandurbar", "Wardha", "Udgir",
    "Hinganghat", "Shegaon", "Shirpur", "Karad", "Chiplun", "Pandharpur",
    "Ratnagiri", "Phaltan", "Talegaon", "Wagholi", "Bhor", "Lonere",
    "Kopargaon", "Sangamner", "Kalyan", "Alibag", "Boisar",
    "Palghar", "Sawantwadi", "Sindhudurg", "Bhandara", "Washim",
    "Buldhana", "Gadchiroli", "Hingoli", "Deorukh", "Ramtek", "Sangola",
    "Warananagar", "Yadrav", "Sinnar", "Akkalkuwa", "Akluj", "Badnera",
    "Bhayander", "Ambejogai", "Avasari", "Babulgaon", "Faizpur",
    "Gadhinglaj", "Jaysingpur", "Kankavli", "Karjat", "Khalapur",
    "Miraj", "New Panvel", "Ohar", "Panhala", "Paniv",
    "Ravet", "Sakoli", "Sevagram", "Shirgaon", "Someshwar Nagar",
    "Tuljapur", "Vasai", "Wadwadi", "Yelgaon", "Andheri", "Vashi",
    "Kharghar", "Kamothe", "Pisoli", "Narhe", "Chikhali", "Haveli",
    "Kuran", "Baramati", "Nile",
]

# Sorted longest-first for greedy substring matching
_SORTED_CITIES: List[str] = sorted(_MAHARASHTRA_CITIES, key=len, reverse=True)

# Words that disqualify a value from being a city
_CITY_REJECT_RE = re.compile(
    r"\b(college|institute|technology|engineering|trust|society|foundation|"
    r"campus|university|polytechnic|management|education|academy|admission|"
    r"merit|cut\s*off|autonomous|private|deemed|research|"
    r"vidyapeeth|vidyalaya|vidhyalaya|technical|shikshan|mandal|sanstha)\b",
    re.IGNORECASE,
)

# Row-level notes/footer detection
_NOTES_RE = re.compile(
    r"(?:cut\s*off\s*indicates|general\s*merit|state\s*level|stage|"
    r"footnote|note\s*:|seat\s*type|indicates|seat\s*matrix|"
    r"merit\s*list|admission\s*note|figures\s*in\s*bracket|"
    r"total\s*seats|important\s*note)",
    re.IGNORECASE,
)

# Files to skip during data-directory scan
_SKIP_PATTERNS: List[str] = [
    "resume", "job", "candidate", "temp_", "backup_",
    "export_", "test_", "_bak", "_old", "sample",
]

# ---------------------------------------------------------------------------
# STAGE 4 -- Branch Normalization helpers
# ---------------------------------------------------------------------------

# Strip redundant acronyms: "Artificial Intelligence (AI)" -> "Artificial Intelligence"
_BRANCH_ACRONYM_STRIP = [
    (re.compile(r"\bArtificial\s+Intelligence\s*\(AI\)\s+and\s+Data\s+Science\b", re.I),
     "Artificial Intelligence and Data Science"),
    (re.compile(r"\bArtificial\s+Intelligence\s*\(AI\)\b", re.I),
     "Artificial Intelligence"),
    (re.compile(r"\bArtificial\s+Intelligence\s+And\s+Machine\s+Learning\s*\(AI\s*[&/]\s*ML\)\b", re.I),
     "Artificial Intelligence And Machine Learning"),
    (re.compile(r"\bArtificial\s+Intelligence\s+And\s+Machine\s+Learning\s*\(AIML\)\b", re.I),
     "Artificial Intelligence And Machine Learning"),
    (re.compile(r"\bComputer\s+Science\s*\(CS\)\b", re.I), "Computer Science"),
    (re.compile(r"\bInformation\s+Technology\s*\(IT\)\b", re.I), "Information Technology"),
    (re.compile(r"\bElectronics\s+And?\s+Telecommunication\s*\(E\s*&?\s*TC\)\b", re.I),
     "Electronics And Telecommunication"),
    (re.compile(r"\bElectronics\s+And?\s+Communication\s*\(E\s*&?\s*C\)\b", re.I),
     "Electronics And Communication"),
    (re.compile(r"\bMechanical\s+Engineering\s*\(ME\)\b", re.I), "Mechanical Engineering"),
    (re.compile(r"\bCivil\s+Engineering\s*\(CE\)\b", re.I), "Civil Engineering"),
    (re.compile(r"\bElectrical\s+Engineering\s*\(EE\)\b", re.I), "Electrical Engineering"),
]

# Long parenthetical -> short acronym
_BRANCH_PAREN_SHORTEN = [
    (re.compile(r"\(Artificial\s+Intelligence\s+And\s+Machine\s+Learning\)", re.I), "(AI And ML)"),
    (re.compile(r"\(Artificial\s+Intelligence\)", re.I), "(AI)"),
    (re.compile(r"\(Machine\s+Learning\)", re.I), "(ML)"),
    (re.compile(r"\(Data\s+Science\)", re.I), "(DS)"),
    (re.compile(r"\(Internet\s+Of\s+Things\)", re.I), "(IoT)"),
    (re.compile(r"\(Cyber\s+Security\)", re.I), "(Cyber Security)"),
    (re.compile(r"\(Cloud\s+Computing\)", re.I), "(Cloud Computing)"),
]

# Acronym fixes after title-case
_ACRONYM_FIXES: Dict[str, str] = {
    "(Ai)": "(AI)", "(Iot)": "(IoT)", "(Ds)": "(DS)",
    "(Cse)": "(CSE)", "(Ml)": "(ML)", "(Vlsi)": "(VLSI)",
    " And ": " and ", " Of ": " of ", " With ": " with ",
    " In ": " in ",  " For ": " for ", " The ": " the ",
}


def _normalize_branch(raw: str) -> Optional[str]:
    """Stage 4 -- Branch Cleaning & Normalization."""
    if not isinstance(raw, str):
        return None
    b = raw.strip()
    if not b or b.lower() in ("nan", "unknown", "none"):
        return None
    if _NOTES_RE.search(b):
        return None

    # Collapse whitespace
    b = re.sub(r"\s+", " ", b).strip()
    # Remove trailing punctuation
    b = b.rstrip(".,;:")
    # Strip redundant acronym-in-paren
    for pattern, replacement in _BRANCH_ACRONYM_STRIP:
        b = pattern.sub(replacement, b)
    # Normalize parentheses: exactly one space before "("
    b = re.sub(r"(?<!\s)\(", " (", b)
    # Remove spaces inside parentheses
    b = re.sub(r"\(\s+([^)]*?)\s+\)", r"(\1)", b)
    # Re-collapse spaces
    b = re.sub(r"\s+", " ", b).strip()
    # Long-paren -> short-paren
    for pattern, replacement in _BRANCH_PAREN_SHORTEN:
        b = pattern.sub(replacement, b)
    # Title Case
    b = b.title()
    # Restore acronyms broken by title case
    for wrong, right in _ACRONYM_FIXES.items():
        b = b.replace(wrong, right)
    # Ensure first char is upper
    return (b[0].upper() + b[1:]) if b else None


# ---------------------------------------------------------------------------
# STAGE 6 -- Category Normalization
# ---------------------------------------------------------------------------

def _normalize_category(raw: str) -> Optional[str]:
    """Stage 6 -- Category Cleaning. Returns canonical string or None."""
    if not isinstance(raw, str):
        return None
    c = raw.strip().upper()
    if not c or c in ("NAN", "UNKNOWN", "NONE"):
        return None
    if _INVALID_CATEGORY_RE.search(c):
        return None

    # Direct lookup
    if c in _CATEGORY_MAP:
        return _CATEGORY_MAP[c]

    # Remove leading G/L prefix and retry
    stripped = re.sub(r"^[GL](?=[A-Z])", "", c)
    if stripped in _CATEGORY_MAP:
        return _CATEGORY_MAP[stripped]

    # Remove trailing S and retry (both with and without prefix)
    if c.endswith("S"):
        s1 = c[:-1]
        if s1 in _CATEGORY_MAP:
            return _CATEGORY_MAP[s1]
        s2 = re.sub(r"^[GL](?=[A-Z])", "", s1)
        if s2 in _CATEGORY_MAP:
            return _CATEGORY_MAP[s2]

    return None


# ---------------------------------------------------------------------------
# STAGE 5 -- City Normalization
# ---------------------------------------------------------------------------

def _normalize_city(
    raw_city: str,
    college_name: str = "",
    college_code: str = "",
    institutes_data: Optional[Dict] = None,
    city_map: Optional[Dict] = None,
) -> Optional[str]:
    """
    Stage 5 -- City Cleaning.
    Validates against canonical Maharashtra city list.
    Falls back to institute address lookup if raw value is absent/corrupt.
    """
    address_str = ""

    # Primary: use raw city if it doesn't look like a college name
    if isinstance(raw_city, str) and raw_city.strip():
        candidate = raw_city.strip()
        if not _CITY_REJECT_RE.search(candidate):
            address_str = candidate

    # Fallback 1: institutes.json location field
    if not address_str and institutes_data and college_code:
        m = re.search(r"(\d{4})", str(college_code))
        if m:
            inst = institutes_data.get(m.group(1), {})
            if isinstance(inst, dict):
                loc = inst.get("location", "")
                if loc:
                    address_str = loc.split(",")[0].strip()

    # Fallback 2: college_city_map.json
    if not address_str and city_map and college_name:
        clean_name = re.sub(r"^\d+\s*-\s*", "", str(college_name).strip())
        mapped = city_map.get(clean_name, "")
        if mapped:
            address_str = str(mapped).strip()

    # Fallback 3: extract from college name after last comma (last resort)
    if not address_str and college_name:
        parts = str(college_name).split(",")
        for part in reversed(parts):
            part = part.strip().rstrip(".")
            if part and not _CITY_REJECT_RE.search(part) and len(part) > 2:
                address_str = part
                break

    if not address_str:
        return None

    # Validate: match against canonical Maharashtra cities (greedy, longest first)
    address_lower = address_str.lower()
    for city in _SORTED_CITIES:
        if city.lower() in address_lower:
            return city

    return None


# ---------------------------------------------------------------------------
# HELPER -- Canonical Deduplication Key
# ---------------------------------------------------------------------------

def _canon_key(s: str) -> str:
    """Lowercase, strips spaces/punctuation/brackets for dedup comparison."""
    return re.sub(r"[\s.,\-()\[\]'\"]+", "", s.lower())


# ---------------------------------------------------------------------------
# HELPER -- Fuzzy Column Mapping
# ---------------------------------------------------------------------------

def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Stage 2 -- Maps raw CSV columns to internal standard names. First match wins."""
    mapping: Dict[str, str] = {}
    already_mapped: Set[str] = set()

    for internal_col, hints in _COLUMN_HINTS.items():
        if internal_col in already_mapped:
            continue
        for hint in hints:
            for raw_col in df.columns:
                if internal_col not in already_mapped and hint.lower() in raw_col.lower():
                    if raw_col not in mapping:
                        mapping[raw_col] = internal_col
                        already_mapped.add(internal_col)
                    break
            if internal_col in already_mapped:
                break

    return df.rename(columns=mapping)


def _extract_code_from_name(name: str) -> Optional[str]:
    """Extracts a 4-digit DTE college code from a name string."""
    m = re.search(r"\b(\d{4})\b", str(name))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# HELPER -- mtime-based cache invalidation
# ---------------------------------------------------------------------------

def _dir_mtime(directory: str) -> float:
    """Returns the max mtime of all CSVs in a directory."""
    try:
        mtimes = [
            os.path.getmtime(os.path.join(directory, f))
            for f in os.listdir(directory)
            if f.endswith(".csv")
        ]
        return max(mtimes) if mtimes else 0.0
    except Exception:
        return 0.0


# ===========================================================================
# MAIN CLASS
# ===========================================================================

class DataLoader:
    """
    Production-grade data loader for DigiPath.

    Single source of truth for all datasets consumed by:
      - CET / Diploma Predictor
      - Institute Search
      - Resume Analyzer
      - Career Recommendation
      - Chatbot / Dashboard

    Implements a 14-stage pipeline with caching, logging, and error isolation.
    All public methods from the original API are preserved unchanged.
    """

    # -- Static helpers (backward compat) -----------------------------------

    @staticmethod
    def clean_city_names(cities: List[str]) -> List[str]:
        cleaned: Set[str] = set()
        for city in cities:
            result = _normalize_city(city)
            if result:
                cleaned.add(result)
        return sorted(cleaned)

    @staticmethod
    def clean_branches(branches: List[str]) -> List[str]:
        seen: Set[str] = set()
        result: List[str] = []
        for b in branches:
            norm = _normalize_branch(b)
            if norm:
                key = _canon_key(norm)
                if key not in seen:
                    seen.add(key)
                    result.append(norm)
        return sorted(result)

    # -- Column constants (re-exported for backward compat) ------------------

    COL_COLLEGE_NAME = COL_COLLEGE_NAME
    COL_COLLEGE_CODE = COL_COLLEGE_CODE
    COL_CITY         = COL_CITY
    COL_BRANCH       = COL_BRANCH
    COL_CATEGORY     = COL_CATEGORY
    COL_CUTOFF       = COL_CUTOFF
    COL_YEAR         = COL_YEAR
    COL_TYPE         = COL_TYPE
    HINTS            = _COLUMN_HINTS

    # -- Constructor ---------------------------------------------------------

    def __init__(self, data_dir: str, institutes_json: str) -> None:
        self.data_dir       = data_dir
        self.institutes_json = institutes_json

        self.institutes_data:   Dict = {}
        self._college_city_map: Dict = {}

        self._cached_df:      Optional[pd.DataFrame] = None
        self._cache_mtime:    float = 0.0
        self._cached_filters: Optional[Dict] = None

        self._load_institutes()
        self._load_city_map()

        log.info(
            "DataLoader ready | data_dir=%s | institutes=%d",
            data_dir, len(self.institutes_data),
        )

    # -- Stage 0 -- Reference data ------------------------------------------

    def _load_institutes(self) -> None:
        if not os.path.exists(self.institutes_json):
            log.warning("institutes.json not found: %s", self.institutes_json)
            return
        try:
            with open(self.institutes_json, "r", encoding="utf-8") as fh:
                self.institutes_data = json.load(fh)
            log.info("institutes.json loaded: %d records", len(self.institutes_data))
        except Exception as exc:
            log.error("Failed to load institutes.json: %s", exc)

    def _load_city_map(self) -> None:
        map_path = os.path.join(self.data_dir, "college_city_map.json")
        if not os.path.exists(map_path):
            return
        try:
            with open(map_path, "r", encoding="utf-8") as fh:
                self._college_city_map = json.load(fh)
            log.info("college_city_map.json loaded: %d entries", len(self._college_city_map))
        except Exception as exc:
            log.error("Failed to load college_city_map.json: %s", exc)

    # -- Stage 1 -- Dataset Discovery ----------------------------------------

    def _should_skip(self, filename: str) -> bool:
        fname_lower = filename.lower()
        return any(p in fname_lower for p in _SKIP_PATTERNS)

    def _discover_prediction_csvs(self) -> List[str]:
        paths: List[str] = []
        try:
            for entry in os.scandir(self.data_dir):
                if not entry.is_file():
                    continue
                if not entry.name.lower().endswith(".csv"):
                    continue
                if self._should_skip(entry.name):
                    log.debug("Skipping: %s", entry.name)
                    continue
                paths.append(entry.path)
        except Exception as exc:
            log.error("Error scanning data dir: %s", exc)
        log.info("Discovered %d prediction CSVs", len(paths))
        return paths

    # -- Stages 2-8 -- Single CSV pipeline ------------------------------------

    def load_dataset(self, path: str) -> pd.DataFrame:
        """
        Public method.
        Loads one CSV through the full cleaning pipeline.
        Returns empty DataFrame if file is invalid, corrupt, or non-prediction.
        """
        filename = os.path.basename(path)

        if not path.endswith(".csv") or self._should_skip(filename):
            return pd.DataFrame()

        try:
            t0 = time.monotonic()
            raw_df = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            log.error("Cannot read %s: %s -- skipping", filename, exc)
            return pd.DataFrame()

        # Stage 2 -- Column mapping
        df = _map_columns(raw_df)

        if COL_CUTOFF not in df.columns:
            log.debug("%s: no cutoff column -- skipping", filename)
            return pd.DataFrame()

        # Ensure essential columns exist
        for col in [COL_COLLEGE_NAME, COL_CITY, COL_BRANCH, COL_CATEGORY]:
            if col not in df.columns:
                df[col] = ""

        # Extract college code if absent
        if COL_COLLEGE_CODE not in df.columns or df[COL_COLLEGE_CODE].isnull().all():
            df[COL_COLLEGE_CODE] = df[COL_COLLEGE_NAME].apply(_extract_code_from_name)

        # Stage 3 -- Row validation: numeric cutoff, drop NaN
        df[COL_CUTOFF] = pd.to_numeric(df[COL_CUTOFF], errors="coerce")
        n_before = len(df)
        df = df.dropna(subset=[COL_CUTOFF])
        n_dropped = n_before - len(df)
        if n_dropped:
            log.debug("%s: dropped %d rows (invalid cutoff)", filename, n_dropped)

        # Stage 3 -- Vectorised notes/footer filter
        mask_notes = (
            df[COL_COLLEGE_NAME].astype(str).str.contains(
                _NOTES_RE.pattern, case=False, na=False, regex=True) |
            df[COL_BRANCH].astype(str).str.contains(
                _NOTES_RE.pattern, case=False, na=False, regex=True) |
            df[COL_CATEGORY].astype(str).str.contains(
                _NOTES_RE.pattern, case=False, na=False, regex=True) |
            df[COL_CITY].astype(str).str.contains(
                _NOTES_RE.pattern, case=False, na=False, regex=True)
        )
        n_notes = int(mask_notes.sum())
        df = df[~mask_notes]
        if n_notes:
            log.debug("%s: dropped %d note/footer rows", filename, n_notes)

        # Stages 4-6 -- Branch / Category / City cleaning (row-wise apply)
        df = df.copy()
        df[COL_BRANCH]   = df[COL_BRANCH].apply(lambda x: _normalize_branch(str(x)))
        df[COL_CATEGORY] = df[COL_CATEGORY].apply(lambda x: _normalize_category(str(x)))
        df[COL_CITY] = df.apply(
            lambda row: _normalize_city(
                str(row[COL_CITY]),
                str(row.get(COL_COLLEGE_NAME, "")),
                str(row.get(COL_COLLEGE_CODE, "")),
                self.institutes_data,
                self._college_city_map,
            ),
            axis=1,
        )

        n_before_clean = len(df)
        df = df.dropna(subset=[COL_BRANCH, COL_CATEGORY, COL_CITY])
        n_clean_dropped = n_before_clean - len(df)
        if n_clean_dropped:
            log.debug(
                "%s: dropped %d rows after branch/category/city cleaning",
                filename, n_clean_dropped,
            )

        # Stage 7 -- Year enrichment from filename
        if COL_YEAR not in df.columns or df[COL_YEAR].isnull().all():
            m = re.search(r"(20\d{2})", filename)
            if m:
                df[COL_YEAR] = int(m.group(1))

        elapsed = time.monotonic() - t0
        log.info(
            "%s -> %d clean rows (from %d) in %.2fs",
            filename, len(df), len(raw_df), elapsed,
        )
        return df

    # -- Stages 9-10 -- Combined dataset with caching -------------------------

    def _is_cache_valid(self) -> bool:
        if self._cached_df is None:
            return False
        return _dir_mtime(self.data_dir) == self._cache_mtime

    def get_all_data(self) -> pd.DataFrame:
        """
        Public method.
        Returns the full combined and cleaned prediction DataFrame.
        Result is cached and automatically invalidated when source files change.
        """
        if self._is_cache_valid():
            log.debug("Cache hit: returning %d-row DataFrame", len(self._cached_df))
            return self._cached_df

        log.info("Cache miss -- rebuilding combined DataFrame...")
        t0 = time.monotonic()

        dfs: List[pd.DataFrame] = []
        for path in self._discover_prediction_csvs():
            try:
                df = self.load_dataset(path)
                if not df.empty:
                    dfs.append(df)
            except Exception as exc:
                log.error("Unexpected error loading %s: %s -- continuing", path, exc)

        if not dfs:
            log.warning("No valid prediction datasets loaded.")
            self._cached_df = pd.DataFrame()
            self._cached_filters = None
            return self._cached_df

        combined = pd.concat(dfs, ignore_index=True)

        # Stage 8 -- Canonical deduplication (keep most recent per college/branch/category)
        if COL_YEAR in combined.columns:
            combined = combined.sort_values(COL_YEAR, ascending=False)

        n_before = len(combined)
        combined = combined.drop_duplicates(
            subset=[COL_COLLEGE_CODE, COL_BRANCH, COL_CATEGORY], keep="first"
        )
        n_dupes = n_before - len(combined)

        elapsed = time.monotonic() - t0
        log.info(
            "Combined: %d rows | %d dupes removed | %.2fs",
            len(combined), n_dupes, elapsed,
        )

        self._cached_df      = combined
        self._cache_mtime    = _dir_mtime(self.data_dir)
        self._cached_filters = None
        return self._cached_df

    # -- Public data accessors -----------------------------------------------

    def get_combined_cet_data(self) -> pd.DataFrame:
        """Returns CET (percentile-based) prediction dataset."""
        return self.get_all_data()

    def get_combined_diploma_data(self) -> pd.DataFrame:
        """Returns Diploma (percentage-based) prediction dataset."""
        return self.get_all_data()

    # -- Stage 11 -- get_unique_filters --------------------------------------

    def get_unique_filters(self) -> Dict:
        """
        Public method.
        Returns cleaned, deduplicated filter values for the Predictor UI.

        Shape:
            {
                "categories":    List[str],
                "branches":      List[str],
                "cities":        List[str],
                "college_types": List[str],
                "advanced":      Dict[str, List[str]],
            }
        """
        if self._cached_filters is not None:
            log.debug("Returning cached filters")
            return self._cached_filters

        t0 = time.monotonic()
        data = self.get_all_data()

        empty_result: Dict = {
            "categories": [], "branches": [], "cities": [],
            "college_types": [], "advanced": {},
        }
        if data.empty:
            self._cached_filters = empty_result
            return empty_result

        # Categories
        seen_cats: Set[str] = set()
        categories: List[str] = []
        for c in data[COL_CATEGORY].dropna().unique():
            if c and c not in seen_cats:
                seen_cats.add(c)
                categories.append(c)
        categories.sort()

        # Branches (canon-key dedup)
        seen_branch_keys: Set[str] = set()
        branches: List[str] = []
        for b in data[COL_BRANCH].dropna().unique():
            k = _canon_key(b)
            if k not in seen_branch_keys:
                seen_branch_keys.add(k)
                branches.append(b)
        branches.sort()

        # Cities (canon-key dedup)
        seen_city_keys: Set[str] = set()
        cities: List[str] = []
        for c in data[COL_CITY].dropna().unique():
            k = _canon_key(c)
            if k not in seen_city_keys:
                seen_city_keys.add(k)
                cities.append(c)
        cities.sort()

        college_types = self._build_college_types()
        advanced      = self._build_advanced_filters(data)

        elapsed = time.monotonic() - t0
        log.info(
            "Filters ready in %.2fs | cat=%d | branch=%d | city=%d | type=%d",
            elapsed, len(categories), len(branches), len(cities), len(college_types),
        )

        result = {
            "categories":    categories,
            "branches":      branches,
            "cities":        cities,
            "college_types": college_types,
            "advanced":      advanced,
        }
        self._cached_filters = result
        return result

    def _build_college_types(self) -> List[str]:
        """Stage 7 -- College type normalization from institutes.json."""
        _type_map = {
            "government autonomous":   "Government Autonomous",
            "government aided":        "Government / Govt-Aided",
            "government / govt-aided": "Government / Govt-Aided",
            "government":              "Government / Govt-Aided",
            "un-aided":                "Un-Aided",
            "unaided":                 "Un-Aided",
            "private":                 "Un-Aided",
            "aided":                   "Government / Govt-Aided",
            "autonomous":              "Autonomous",
            "non-autonomous":          "Non-Autonomous",
            "deemed":                  "Deemed University",
            "university":              "University",
            "grant-in-aid":            "Government / Govt-Aided",
        }
        seen: Set[str] = set()
        types: List[str] = []
        for info in self.institutes_data.values():
            if not isinstance(info, dict):
                continue
            ov = info.get("system_overview", {})
            if not isinstance(ov, dict):
                continue
            for field in ("status", "autonomy"):
                raw = str(ov.get(field, "")).strip().lower()
                if not raw:
                    continue
                canonical = _type_map.get(raw, raw.title())
                if canonical and canonical not in seen:
                    seen.add(canonical)
                    types.append(canonical)
        return sorted(types)

    def _build_advanced_filters(self, data: pd.DataFrame) -> Dict:
        """Stage 9 -- Discovers additional filterable columns."""
        core = {COL_COLLEGE_NAME, COL_COLLEGE_CODE, COL_CITY,
                COL_BRANCH, COL_CATEGORY, COL_CUTOFF, COL_YEAR, COL_TYPE}
        advanced: Dict = {}
        for col in data.columns:
            if col in core or data[col].dtype != "object":
                continue
            vals = [
                str(x).strip()
                for x in data[col].dropna().unique()
                if str(x).strip() and str(x).strip().lower() not in ("nan", "unknown", "")
            ]
            if 2 <= len(vals) <= 50:
                advanced[col] = sorted(vals)
        return advanced

    # -- College type lookup (used by predictors) ----------------------------

    def get_college_type(self, college_code: str) -> str:
        inst = self.institutes_data.get(str(college_code))
        if not inst or not isinstance(inst, dict):
            return "Unknown"
        ov = inst.get("system_overview", {})
        status   = ov.get("status", "")   if isinstance(ov, dict) else ""
        autonomy = ov.get("autonomy", "") if isinstance(ov, dict) else ""
        return f"{status} {autonomy}".strip() or "Unknown"

    def extract_code_from_name(self, name: str) -> Optional[str]:
        return _extract_code_from_name(name)

    def map_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        return _map_columns(df)

    # -- Specialised dataset accessors (original API) ------------------------

    def get_job_data(self) -> pd.DataFrame:
        path = os.path.join(self.data_dir, "JobsDatasetProcessed.csv")
        if os.path.exists(path):
            try:
                return pd.read_csv(path, low_memory=False)
            except Exception as exc:
                log.error("Failed to load job data: %s", exc)
        return pd.DataFrame()

    def get_candidate_job_data(self) -> pd.DataFrame:
        path = os.path.join(self.data_dir, "candidate_job_role_dataset.csv")
        if os.path.exists(path):
            try:
                return pd.read_csv(path, low_memory=False)
            except Exception as exc:
                log.error("Failed to load candidate job data: %s", exc)
        return pd.DataFrame()

    def get_resume_dataset(self) -> pd.DataFrame:
        path = os.path.join(self.data_dir, "Resume.csv")
        if os.path.exists(path):
            try:
                return pd.read_csv(path, low_memory=False)
            except Exception as exc:
                log.error("Failed to load resume dataset: %s", exc)
        return pd.DataFrame()

    def get_placement_data(self) -> Dict:
        return self.institutes_data

    # -- Stage 13 -- Verification --------------------------------------------

    def verify(self) -> Dict:
        """
        Runs full post-load verification and prints a diagnostic report.
        Returns a summary dict.
        """
        sep = "=" * 60
        log.info(sep)
        log.info("DATALOADER VERIFICATION REPORT")
        log.info(sep)

        data    = self.get_all_data()
        filters = self.get_unique_filters()

        colleges = data[COL_COLLEGE_NAME].nunique() if not data.empty else 0
        branches = filters["branches"]
        cities   = filters["cities"]
        cats     = filters["categories"]
        ctypes   = filters["college_types"]

        log.info("Total rows           : %d", len(data))
        log.info("Total colleges       : %d", colleges)
        log.info("Total branches       : %d", len(branches))
        log.info("Total cities         : %d", len(cities))
        log.info("Total categories     : %d", len(cats))
        log.info("Total college types  : %d", len(ctypes))
        log.info("")
        log.info("First 20 branches    : %s", branches[:20])
        log.info("First 20 cities      : %s", cities[:20])
        log.info("All categories       : %s", cats)
        log.info(sep)

        return {
            "total_rows":          len(data),
            "total_colleges":      colleges,
            "total_branches":      len(branches),
            "total_cities":        len(cities),
            "total_categories":    len(cats),
            "total_college_types": len(ctypes),
            "sample_branches":     branches[:20],
            "sample_cities":       cities[:20],
            "categories":          cats,
        }
