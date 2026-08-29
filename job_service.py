"""Lazy-loaded, deterministic matching over local job datasets."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def _find_column(columns: Iterable[str], candidates: tuple[str, ...]) -> Optional[str]:
    normalized = {str(column).strip().casefold(): column for column in columns}
    return next((normalized[candidate] for candidate in candidates if candidate in normalized), None)


def _normalize_skill_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#.]+", " ", value.casefold())).strip()


class JobService:
    """Process-wide singleton that reads each local job CSV once on first use."""

    _instance: Optional["JobService"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, data_dir: str | Path = "data") -> "JobService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, data_dir: str | Path = "data") -> None:
        if self._initialized:
            return
        self.data_dir = Path(data_dir)
        self._load_lock = threading.Lock()
        self._loaded = False
        self._jobs = pd.DataFrame()
        self._candidate_roles = pd.DataFrame()
        self._tfidf_vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2), sublinear_tf=True)
        self._count_vectorizer = CountVectorizer(lowercase=True, binary=True, token_pattern=r"(?u)\b[\w+#.][\w+#.\-]*\b")
        self._job_tfidf = None
        self._job_binary = None
        self._role_skills: dict[str, str] = {}
        self._initialized = True

    @classmethod
    def get_instance(cls, data_dir: str | Path = "data") -> "JobService":
        return cls(data_dir)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            jobs_path = self.data_dir / "JobsDatasetProcessed.csv"
            candidates_path = self.data_dir / "candidate_job_role_dataset.csv"
            if not jobs_path.is_file() or not candidates_path.is_file():
                self._loaded = True
                return
            jobs = pd.read_csv(jobs_path, low_memory=False)
            candidates = pd.read_csv(candidates_path, low_memory=False)
            self._jobs = self._normalize_jobs(jobs)
            self._candidate_roles = candidates.copy()
            self._role_skills = self._build_role_skill_index(candidates)
            documents = self._jobs["_skill_document"].fillna("").astype(str).tolist()
            if documents:
                self._job_tfidf = self._tfidf_vectorizer.fit_transform(documents)
                self._job_binary = self._count_vectorizer.fit_transform(documents)
            self._loaded = True

    def _normalize_jobs(self, frame: pd.DataFrame) -> pd.DataFrame:
        title_column = _find_column(frame.columns, ("job title", "title"))
        company_column = _find_column(frame.columns, ("company", "company name", "employer"))
        location_column = _find_column(frame.columns, ("location", "job location"))
        url_column = _find_column(frame.columns, ("source url", "job url", "url", "link"))
        it_skills_column = _find_column(frame.columns, ("it skills", "skills", "required skills"))
        soft_skills_column = _find_column(frame.columns, ("soft skills",))
        description_column = _find_column(frame.columns, ("description", "job description", "query"))
        normalized = pd.DataFrame(index=frame.index)
        normalized["job_title"] = frame[title_column].astype("string").str.strip() if title_column else pd.Series("", index=frame.index, dtype="string")
        normalized["company"] = frame[company_column].astype("string").str.strip() if company_column else pd.Series(pd.NA, index=frame.index, dtype="string")
        normalized["location"] = frame[location_column].astype("string").str.strip() if location_column else pd.Series(pd.NA, index=frame.index, dtype="string")
        normalized["source_url"] = frame[url_column].astype("string").str.strip() if url_column else pd.Series(pd.NA, index=frame.index, dtype="string")
        required = frame[it_skills_column].astype("string").fillna("") if it_skills_column else pd.Series("", index=frame.index, dtype="string")
        soft = frame[soft_skills_column].astype("string").fillna("") if soft_skills_column else pd.Series("", index=frame.index, dtype="string")
        description = frame[description_column].astype("string").fillna("") if description_column else pd.Series("", index=frame.index, dtype="string")
        normalized["required_skills"] = required.str.replace(r"\s+", " ", regex=True).str.strip()
        normalized["_skill_document"] = (normalized["required_skills"].fillna("") + " " + soft + " " + description).map(_normalize_skill_text)
        return normalized.loc[normalized["job_title"].notna() & normalized["job_title"].ne("")].reset_index(drop=True)

    def _build_role_skill_index(self, frame: pd.DataFrame) -> dict[str, str]:
        role_column = _find_column(frame.columns, ("job_role", "job role", "role"))
        skills_column = _find_column(frame.columns, ("skills", "technical skills"))
        if role_column is None or skills_column is None:
            return {}
        normalized = pd.DataFrame({"role": frame[role_column].astype("string").str.strip(), "skills": frame[skills_column].astype("string").fillna("")})
        normalized = normalized.loc[normalized["role"].notna() & normalized["role"].ne("")]
        grouped = normalized.groupby("role", sort=False)["skills"].agg(" ".join)
        return {str(role): _normalize_skill_text(str(skills)) for role, skills in grouped.items()}

    def match_jobs(self, candidate_skills: Iterable[str], target_role: Optional[str] = None, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        self._ensure_loaded()
        safe_limit = min(max(int(limit), 1), 100)
        safe_offset = max(int(offset), 0)
        explicit_skills = " ".join(str(skill) for skill in candidate_skills if str(skill).strip())
        role_skills = self._role_skills.get(str(target_role or ""), "")
        candidate_document = _normalize_skill_text(f"{explicit_skills} {role_skills}")
        if self._jobs.empty or not candidate_document or self._job_tfidf is None or self._job_binary is None:
            return {"items": [], "total": 0, "limit": safe_limit, "offset": safe_offset}
        query_tfidf = self._tfidf_vectorizer.transform([candidate_document])
        query_binary = self._count_vectorizer.transform([candidate_document])
        cosine_scores = cosine_similarity(query_tfidf, self._job_tfidf).ravel()
        intersections = self._job_binary.minimum(query_binary).sum(axis=1).A1
        unions = self._job_binary.maximum(query_binary).sum(axis=1).A1
        jaccard_scores = np.divide(intersections, unions, out=np.zeros_like(intersections, dtype=float), where=unions != 0)
        scores = np.clip((cosine_scores * 0.7 + jaccard_scores * 0.3) * 100.0, 0.0, 100.0)
        ranked = self._jobs.assign(match_score=np.rint(scores).astype(int)).sort_values(["match_score", "job_title"], ascending=[False, True], kind="stable")
        page = ranked.iloc[safe_offset : safe_offset + safe_limit].loc[:, ["job_title", "company", "location", "match_score", "required_skills", "source_url"]]
        page = page.replace({pd.NA: None, np.nan: None})
        return {"items": page.to_dict(orient="records"), "total": int(len(ranked)), "limit": safe_limit, "offset": safe_offset}
