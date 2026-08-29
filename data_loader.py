"""Strict, manifest-driven admissions data ingestion for DigiPath."""

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
COL_STATE_RANK = "state_rank"

MANIFEST: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "fe_2024.csv": MappingProxyType({"exam_type": "CET", "year": 2024, "metric": "percentile"}),
        "fe_2025.csv": MappingProxyType({"exam_type": "CET", "year": 2025, "metric": "percentile"}),
    }
)

REJECTED_DATASETS = frozenset(
    {
        "merged_data.csv",
        "final_clean_full.csv",
        "data.csv",
        "converted_college_data_final.csv",
        "cap_cutoff_data.csv",
    }
)

_COLUMN_ALIASES: Mapping[str, Sequence[str]] = MappingProxyType(
    {
        COL_COLLEGE_NAME: ("college name", "institute name"),
        COL_COLLEGE_CODE: ("college code", "dte code", "college_code"),
        COL_CITY: ("city", "location", "district"),
        COL_BRANCH: ("branch", "course name", "course"),
        COL_CATEGORY: ("category", "seat category"),
        COL_CUTOFF: ("cutoff", "percentile", "percentage", "percent"),
        COL_QUOTA: ("quota",),
        COL_SEAT_TYPE: ("seat type", "gender"),
        COL_STATE_RANK: ("cutoff rank", "state rank", "rank"),
    }
)

_CATEGORY_MAP: Mapping[str, str] = MappingProxyType(
    {
        "OPEN": "OPEN", "GOPEN": "OPEN", "GOPENS": "OPEN", "LOPEN": "OPEN", "LOPENS": "OPEN",
        "OBC": "OBC", "GOBC": "OBC", "GOBCS": "OBC", "LOBC": "OBC", "LOBCS": "OBC",
        "SC": "SC", "GSC": "SC", "GSCS": "SC", "LSC": "SC", "LSCS": "SC",
        "ST": "ST", "GST": "ST", "GSTS": "ST", "LST": "ST", "LSTS": "ST",
        "EWS": "EWS", "GEWS": "EWS", "LEWS": "EWS",
        "SEBC": "SEBC", "GSEBC": "SEBC", "LSEBC": "SEBC",
        "VJ": "VJ", "VJA": "VJ", "GVJ": "VJ", "LVJ": "VJ",
        "NTA": "NT-A", "NT-A": "NT-A", "NT1": "NT-A", "GNT1": "NT-A", "LNT1": "NT-A",
        "NTB": "NT-B", "NT-B": "NT-B", "NT2": "NT-B", "GNT2": "NT-B", "LNT2": "NT-B",
        "NTC": "NT-C", "NT-C": "NT-C", "NT3": "NT-C", "GNT3": "NT-C", "LNT3": "NT-C",
        "NTD": "NT-D", "NT-D": "NT-D", "NT4": "NT-D", "GNT4": "NT-D", "LNT4": "NT-D",
        "TFWS": "TFWS", "PWD": "PWD", "PH": "PWD", "OCI": "OCI", "CIWGC": "CIWGC",
    }
)

_BRANCH_TAXONOMY: Mapping[str, str] = MappingProxyType(
    {
        "cse": "Computer Engineering",
        "computer science": "Computer Engineering",
        "computer science and engineering": "Computer Engineering",
        "computer engineering": "Computer Engineering",
        "it": "Information Technology",
        "information technology": "Information Technology",
        "ai and ds": "Artificial Intelligence and Data Science",
        "ai ds": "Artificial Intelligence and Data Science",
        "artificial intelligence and data science": "Artificial Intelligence and Data Science",
        "e and tc": "Electronics and Telecommunication Engineering",
        "e tc": "Electronics and Telecommunication Engineering",
        "electronics and telecommunication": "Electronics and Telecommunication Engineering",
        "electronics and telecommunication engg": "Electronics and Telecommunication Engineering",
        "electronics and telecommunication engineering": "Electronics and Telecommunication Engineering",
        "mechanical": "Mechanical Engineering",
        "mechanical engg": "Mechanical Engineering",
        "mechanical engineering": "Mechanical Engineering",
        "civil": "Civil Engineering",
        "civil engg": "Civil Engineering",
        "civil engineering": "Civil Engineering",
    }
)

_CITY_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "mumbai": "Mumbai", "navi mumbai": "Navi Mumbai", "pune": "Pune", "nagpur": "Nagpur",
        "thane": "Thane", "nashik": "Nashik", "nasik": "Nashik", "aurangabad": "Chhatrapati Sambhajinagar",
        "chhatrapati sambhajinagar": "Chhatrapati Sambhajinagar", "solapur": "Solapur", "amravati": "Amravati",
        "nanded": "Nanded", "kolhapur": "Kolhapur", "sangli": "Sangli", "latur": "Latur", "dhule": "Dhule",
        "ahmednagar": "Ahmednagar", "chandrapur": "Chandrapur", "parbhani": "Parbhani", "jalna": "Jalna",
        "beed": "Beed", "gondia": "Gondia", "satara": "Satara", "yavatmal": "Yavatmal", "wardha": "Wardha",
        "ratnagiri": "Ratnagiri", "palghar": "Palghar", "raigad": "Raigad", "akola": "Akola",
        "buldhana": "Buldhana", "bhandara": "Bhandara", "washim": "Washim", "hingoli": "Hingoli",
        "osmanabad": "Dharashiv", "dharashiv": "Dharashiv", "panvel": "Panvel", "kalyan": "Kalyan",
        "ulhasnagar": "Ulhasnagar", "vasai": "Vasai", "vashi": "Vashi", "kharghar": "Kharghar",
        "andheri": "Andheri", "baramati": "Baramati", "karad": "Karad", "miraj": "Miraj",
    }
)

_CITY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(alias) for alias in sorted(_CITY_ALIASES, key=len, reverse=True)) + r")\b",
    flags=re.IGNORECASE,
)


def _normalise_column_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().casefold())


def _normalise_branch_key(value: pd.Series) -> pd.Series:
    return (
        value.astype("string")
        .str.casefold()
        .str.replace("&", " and ", regex=False)
        .str.replace(r"[^a-z0-9]+", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def _normalize_branch(value: str) -> Optional[str]:
    """Return a canonical branch label for a single external value."""
    series = pd.Series([value], dtype="string")
    normalized = _normalise_branch_key(series).map(_BRANCH_TAXONOMY).fillna(series.str.strip().str.title())
    result = normalized.iloc[0]
    return None if pd.isna(result) or not str(result).strip() else str(result)


def _normalise_city_series(values: pd.Series) -> pd.Series:
    candidates = values.astype("string").str.strip()
    extracted = candidates.str.extract(_CITY_PATTERN, expand=False)
    return extracted.str.casefold().map(_CITY_ALIASES).fillna(candidates.str.title())


def _normalize_city(raw_city: str) -> Optional[str]:
    """Resolve a city or district without rejecting institution/address text."""
    if raw_city is None or pd.isna(raw_city):
        return None
    value = str(raw_city).strip()
    if not value:
        return None
    match = _CITY_PATTERN.search(value)
    return _CITY_ALIASES[match.group(1).casefold()] if match else value.title()


def _normalise_dte_codes(values: pd.Series, names: pd.Series) -> pd.Series:
    source_codes = values.astype("string").str.extract(r"(\d{1,5})", expand=False)
    embedded_codes = names.astype("string").str.extract(r"\b(\d{1,5})\b", expand=False)
    selected = source_codes.fillna(embedded_codes)
    numeric_codes = pd.to_numeric(selected, errors="coerce").astype("Int64")
    return numeric_codes.astype("string").str.zfill(5)


def _canonical_category(values: pd.Series) -> pd.Series:
    source = values.astype("string").str.strip().str.upper()
    direct = source.map(_CATEGORY_MAP)
    stripped = source.str.replace(r"^[GL]", "", regex=True).str.replace(r"S$", "", regex=True)
    return direct.fillna(stripped.map(_CATEGORY_MAP))


class DataLoader:
    """Loads one canonical, validated admissions fact table from a fixed manifest."""

    COL_COLLEGE_NAME = COL_COLLEGE_NAME
    COL_COLLEGE_CODE = COL_COLLEGE_CODE
    COL_CITY = COL_CITY
    COL_BRANCH = COL_BRANCH
    COL_CATEGORY = COL_CATEGORY
    COL_CUTOFF = COL_CUTOFF
    COL_YEAR = COL_YEAR
    COL_TYPE = COL_TYPE
    HINTS = _COLUMN_ALIASES

    def __init__(self, data_dir: str | Path, institutes_json: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.institutes_json = Path(institutes_json)
        self.city_map_path = self.data_dir / "college_city_map.json"
        self.institutes_data: Dict[str, Any] = {}
        self._college_city_map: Dict[str, str] = {}
        self._college_city_by_code: Dict[str, str] = {}
        self._college_type_by_code: Dict[str, str] = {}
        self._cached_df: Optional[pd.DataFrame] = None
        self._cached_filters: Optional[Dict[str, Any]] = None
        self._cache_signature: Optional[tuple[tuple[str, Optional[int]], ...]] = None
        self._load_institutes()
        self._load_city_map()

    @staticmethod
    def clean_city_names(cities: List[str]) -> List[str]:
        normalized = _normalise_city_series(pd.Series(cities, dtype="string")).dropna().unique().tolist()
        return sorted(str(city) for city in normalized if str(city).strip())

    @staticmethod
    def clean_branches(branches: List[str]) -> List[str]:
        source = pd.Series(branches, dtype="string")
        normalized = _normalise_branch_key(source).map(_BRANCH_TAXONOMY).fillna(source.str.strip().str.title())
        return sorted(str(branch) for branch in normalized.dropna().unique() if str(branch).strip())

    def _load_institutes(self) -> None:
        if not self.institutes_json.is_file():
            log.warning("Institute metadata file is missing: %s", self.institutes_json)
            return
        try:
            with self.institutes_json.open("r", encoding="utf-8") as file_handle:
                raw = json.load(file_handle)
            self.institutes_data = raw if isinstance(raw, dict) else {}
            normalized_keys = pd.Series(list(self.institutes_data), dtype="string")
            canonical_keys = _normalise_dte_codes(normalized_keys, normalized_keys)
            records = pd.Series(list(self.institutes_data.values()), dtype="object")
            metadata = pd.DataFrame({"college_code": canonical_keys, "record": records}).dropna(subset=["college_code"])
            locations = metadata["record"].map(lambda item: item.get("location", "") if isinstance(item, dict) else "")
            self._college_city_by_code = dict(zip(metadata["college_code"], _normalise_city_series(locations)))
            system = metadata["record"].map(lambda item: item.get("system_overview", {}) if isinstance(item, dict) else {})
            status = system.map(lambda item: item.get("status", "") if isinstance(item, dict) else "").astype("string").str.strip()
            autonomy = system.map(lambda item: item.get("autonomy", "") if isinstance(item, dict) else "").astype("string").str.strip()
            college_type = (status + " " + autonomy).str.replace(r"\s+", " ", regex=True).str.strip().replace("", "Unknown")
            self._college_type_by_code = dict(zip(metadata["college_code"], college_type))
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

    def _discover_prediction_csvs(self) -> List[Path]:
        paths = [self.data_dir / filename for filename in MANIFEST if (self.data_dir / filename).is_file()]
        missing = [filename for filename in MANIFEST if not (self.data_dir / filename).is_file()]
        if missing:
            log.warning("Manifest sources missing: %s", ", ".join(missing))
        return paths

    def _source_signature(self) -> tuple[tuple[str, Optional[int]], ...]:
        tracked_paths = [self.data_dir / filename for filename in MANIFEST]
        tracked_paths.extend([self.institutes_json, self.city_map_path])
        return tuple(sorted((str(path), path.stat().st_mtime_ns if path.exists() else None) for path in tracked_paths))

    def _is_cache_valid(self) -> bool:
        return self._cached_df is not None and self._cache_signature == self._source_signature()

    def map_columns(self, frame: pd.DataFrame) -> pd.DataFrame:
        lookup = {_normalise_column_name(column): column for column in frame.columns}
        rename_map: Dict[str, str] = {}
        for canonical, aliases in _COLUMN_ALIASES.items():
            source = next((lookup[alias] for alias in aliases if alias in lookup), None)
            if source is not None:
                rename_map[source] = canonical
        return frame.rename(columns=rename_map)

    def load_dataset(self, path: str | Path) -> pd.DataFrame:
        source_path = Path(path)
        source_name = source_path.name.casefold()
        metadata = MANIFEST.get(source_name)
        if metadata is None:
            if source_name in REJECTED_DATASETS:
                log.info("Rejected overlapping dataset: %s", source_path.name)
            else:
                log.warning("Rejected undeclared dataset: %s", source_path.name)
            return pd.DataFrame()
        if metadata["metric"] != "percentile":
            raise ValueError(f"Unsupported declared metric: {metadata['metric']}")

        try:
            frame = pd.read_csv(source_path, low_memory=False)
        except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            log.error("Unable to read %s: %s", source_path.name, exc)
            return pd.DataFrame()

        frame = self.map_columns(frame)
        required = {COL_COLLEGE_NAME, COL_CITY, COL_BRANCH, COL_CATEGORY, COL_CUTOFF}
        missing = required.difference(frame.columns)
        if missing:
            log.error("Rejected %s; required columns missing: %s", source_path.name, sorted(missing))
            return pd.DataFrame()

        college_name = frame[COL_COLLEGE_NAME].astype("string").str.strip().str.title()
        source_codes = frame[COL_COLLEGE_CODE] if COL_COLLEGE_CODE in frame.columns else pd.Series(pd.NA, index=frame.index, dtype="string")
        college_code = _normalise_dte_codes(source_codes, college_name)
        city_source = frame[COL_CITY].astype("string").str.strip()
        city_from_code = college_code.map(self._college_city_by_code)
        city_from_name = college_name.map(self._college_city_map)
        city = _normalise_city_series(city_source.mask(city_source.isin(["", "nan", "none"]), pd.NA).fillna(city_from_code).fillna(city_from_name))
        branch_source = frame[COL_BRANCH].astype("string").str.strip()
        branch = _normalise_branch_key(branch_source).map(_BRANCH_TAXONOMY).fillna(branch_source.str.title())
        category_source = frame[COL_CATEGORY].astype("string").str.strip().str.upper()
        category = _canonical_category(category_source)
        seat_type = frame[COL_SEAT_TYPE].astype("string").str.strip().str.upper() if COL_SEAT_TYPE in frame.columns else category_source.str.extract(r"^([GL])", expand=False).map({"G": "GENERAL", "L": "LADIES"}).fillna("UNSPECIFIED")
        quota = frame[COL_QUOTA].astype("string").str.strip().str.upper() if COL_QUOTA in frame.columns else pd.Series("STATE", index=frame.index, dtype="string")
        percentile = pd.to_numeric(frame[COL_CUTOFF], errors="coerce").astype("float64")
        valid_percentile = percentile.notna() & percentile.between(0.0, 100.0, inclusive="both")
        state_rank = pd.to_numeric(frame[COL_STATE_RANK], errors="coerce").astype("Int64") if COL_STATE_RANK in frame.columns else pd.Series(pd.NA, index=frame.index, dtype="Int64")

        normalized = pd.DataFrame(
            {
                COL_COLLEGE_CODE: college_code,
                COL_COLLEGE_NAME: college_name,
                COL_CITY: city,
                COL_BRANCH: branch,
                COL_CATEGORY: category,
                COL_QUOTA: quota,
                COL_SEAT_TYPE: seat_type,
                COL_CUTOFF: percentile,
                COL_STATE_RANK: state_rank,
                COL_EXAM_TYPE: metadata["exam_type"],
                COL_YEAR: int(metadata["year"]),
            }
        )
        normalized[COL_TYPE] = normalized[COL_COLLEGE_CODE].map(self._college_type_by_code).fillna("Unknown")
        valid_rows = valid_percentile & normalized[[COL_COLLEGE_CODE, COL_COLLEGE_NAME, COL_CITY, COL_BRANCH, COL_CATEGORY]].notna().all(axis=1)
        normalized = normalized.loc[valid_rows].copy()
        if normalized.empty:
            log.warning("No valid percentile admissions rows remain in %s", source_path.name)
        return normalized

    def get_all_data(self) -> pd.DataFrame:
        if self._is_cache_valid():
            return self._cached_df
        frames = [self.load_dataset(path) for path in self._discover_prediction_csvs()]
        usable_frames = [frame for frame in frames if not frame.empty]
        if not usable_frames:
            self._cached_df = pd.DataFrame(columns=[COL_COLLEGE_CODE, COL_COLLEGE_NAME, COL_CITY, COL_BRANCH, COL_CATEGORY, COL_QUOTA, COL_SEAT_TYPE, COL_CUTOFF, COL_STATE_RANK, COL_EXAM_TYPE, COL_YEAR, COL_TYPE])
        else:
            combined = pd.concat(usable_frames, ignore_index=True)
            grain = [COL_COLLEGE_CODE, COL_BRANCH, COL_CATEGORY, COL_QUOTA, COL_SEAT_TYPE, COL_EXAM_TYPE, COL_YEAR]
            self._cached_df = combined.drop_duplicates(subset=grain, keep="last").sort_values(grain, kind="stable").reset_index(drop=True)
        self._cached_filters = None
        self._cache_signature = self._source_signature()
        log.info("Loaded %d canonical admissions rows.", len(self._cached_df))
        return self._cached_df

    def get_combined_cet_data(self) -> pd.DataFrame:
        return self.get_all_data().loc[lambda frame: frame[COL_EXAM_TYPE].eq("CET")].copy()

    def get_combined_diploma_data(self) -> pd.DataFrame:
        return self.get_all_data().loc[lambda frame: frame[COL_EXAM_TYPE].eq("DIPLOMA")].copy()

    def get_unique_filters(self) -> Dict[str, Any]:
        if self._cached_filters is not None and self._is_cache_valid():
            return self._cached_filters
        data = self.get_all_data()
        result = {
            "categories": sorted(data[COL_CATEGORY].dropna().astype(str).unique().tolist()),
            "branches": sorted(data[COL_BRANCH].dropna().astype(str).unique().tolist()),
            "cities": sorted(data[COL_CITY].dropna().astype(str).unique().tolist()),
            "college_types": sorted(data[COL_TYPE].dropna().astype(str).unique().tolist()),
            "advanced": {"quota": sorted(data[COL_QUOTA].dropna().astype(str).unique().tolist()), "seat_type": sorted(data[COL_SEAT_TYPE].dropna().astype(str).unique().tolist())},
        }
        self._cached_filters = result
        return result

    def get_college_type(self, college_code: str) -> str:
        canonical = _normalise_dte_codes(pd.Series([college_code], dtype="string"), pd.Series([college_code], dtype="string")).iloc[0]
        return self._college_type_by_code.get(str(canonical), "Unknown")

    def extract_code_from_name(self, name: str) -> Optional[str]:
        result = _normalise_dte_codes(pd.Series([pd.NA], dtype="string"), pd.Series([name], dtype="string")).iloc[0]
        return None if pd.isna(result) else str(result)

    def get_job_data(self) -> pd.DataFrame:
        path = self.data_dir / "JobsDatasetProcessed.csv"
        return pd.read_csv(path, low_memory=False) if path.is_file() else pd.DataFrame()

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
            "exam_types": sorted(data[COL_EXAM_TYPE].dropna().astype(str).unique().tolist()),
            "years": sorted(data[COL_YEAR].dropna().astype(int).unique().tolist()),
        }
