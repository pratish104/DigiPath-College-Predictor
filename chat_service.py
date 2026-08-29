"""Lazy chatbot retrieval bound to the injected canonical DataLoader repository."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from data_loader import COL_BRANCH, COL_CATEGORY, COL_COLLEGE_NAME, COL_CUTOFF, DataLoader


class ChatService:
    """Builds a corpus lazily from the supplied central repository, never from CSV paths."""

    def __init__(self, data_loader: DataLoader) -> None:
        self.data_loader = data_loader
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        self.corpus: list[str] = []
        self.source_map: list[dict[str, Any]] = []
        self.tfidf_matrix = None
        self._built = False

    def _build_corpus(self) -> None:
        if self._built:
            return
        institutes = self.data_loader.institutes_data
        institute_items = [(str(code), info) for code, info in institutes.items() if isinstance(info, dict)]
        institute_text = [
            f"Institute {info.get('name', 'Unknown')} ({code}) located in {info.get('location', 'Maharashtra')}. Status: {(info.get('system_overview') or {}).get('status', 'Unknown')}."
            for code, info in institute_items
        ]
        institute_sources = [{"type": "institute", "id": code, "name": info.get("name", "Unknown")} for code, info in institute_items]
        cutoff_data = self.data_loader.get_combined_cet_data()
        grouped = cutoff_data.groupby([COL_COLLEGE_NAME, COL_BRANCH, COL_CATEGORY], dropna=False)[COL_CUTOFF].agg(["min", "max", "mean"]).reset_index().sort_values([COL_COLLEGE_NAME, COL_BRANCH, COL_CATEGORY], kind="stable").head(1000)
        cutoff_text = [
            f"Cutoff for {record[COL_COLLEGE_NAME]} branch {record[COL_BRANCH]} category {record[COL_CATEGORY]} ranges from {record['min']:.2f} to {record['max']:.2f} with average {record['mean']:.2f}."
            for record in grouped.to_dict(orient="records")
        ]
        cutoff_sources = [{"type": "cutoff", "data": record} for record in grouped.to_dict(orient="records")]
        platform_text = [
            "DigiPath provides deterministic college eligibility bands from validated historical MHT CET cutoff data.",
            "DigiPath resume analysis uses offline document parsing and role skill matching.",
        ]
        self.corpus = institute_text + cutoff_text + platform_text
        self.source_map = institute_sources + cutoff_sources + [{"type": "platform"} for _ in platform_text]
        if self.corpus:
            self.tfidf_matrix = self.vectorizer.fit_transform(self.corpus)
        self._built = True

    def query(self, user_query: str) -> str:
        self._build_corpus()
        if not self.corpus or self.tfidf_matrix is None:
            return "Insufficient validated dataset information is available."
        query_vector = self.vectorizer.transform([user_query])
        scores = cosine_similarity(query_vector, self.tfidf_matrix).ravel()
        best_index = int(np.argmax(scores))
        if scores[best_index] < 0.15:
            return "No validated match was found. Ask about a specific institute, branch, or cutoff category."
        source = self.source_map[best_index]
        if source["type"] == "institute":
            return f"Institute information: {self.corpus[best_index]}"
        if source["type"] == "cutoff":
            return f"Historical cutoff information: {self.corpus[best_index]}"
        return self.corpus[best_index]

    def get_contextual_response(self, user_query: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        normalized = user_query.casefold()
        if any(token in normalized for token in ("predict", "my score", "percentile", "get into")):
            return "Use the Predictor module for a deterministic eligibility analysis based on your percentile and category."
        if any(token in normalized for token in ("resume", "cv", "ats")):
            return "Use Resume AI to analyze a PDF or DOCX under the document safety limits."
        return self.query(user_query)
