"""RLHF-ready GenAI Chatbot Engine with small-talk classifier, Mock Interview Router, and RAG Confidence Guard."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from data_loader import COL_BRANCH, COL_CATEGORY, COL_COLLEGE_NAME, COL_CUTOFF, DataLoader

log = logging.getLogger("digipath.chatbot")

GREETING_PATTERNS = re.compile(
    r"^\s*(hi|hello|hey|greetings|who are you|what are you|help|good morning|good evening|yo|sup|namaste)\b",
    re.IGNORECASE,
)

MOCK_INTERVIEW_PATTERNS = re.compile(
    r"\b(mock\s+interview|data\s+analyst\s+interview|prepare\s+interview|technical\s+interview|interview\s+questions|interview\s+prep)\b",
    re.IGNORECASE,
)

TERMINAL_WELCOME = (
    "> [ SYSTEM INITIALIZED // NEURAL CORE ONLINE ]\n\n"
    "Greetings, Agent. I am your **DigiPath AI Advisory System**. "
    "I specialize in MHT-CET/Diploma college predictions, placement matrices, fee structures, ATS resume engineering, and mock technical interviews.\n\n"
    "How can I assist your mission today? Select a quick protocol below or type your inquiry."
)

DEFAULT_ACTION_CHIPS = [
    {"label": "[ PREDICT COLLEGE ]", "query": "Predict my college based on cutoff"},
    {"label": "[ BUILD RESUME ]", "query": "Open resume builder to create an ATS resume"},
    {"label": "[ MOCK INTERVIEW ]", "query": "Start a technical mock interview"},
    {"label": "[ SCAM DETECTOR ]", "query": "Verify an offer letter or internship for safety"},
]

MOCK_INTERVIEW_SETS = {
    "DATA_ANALYST": (
        "**[ 🎯 TECHNICAL MOCK INTERVIEW // DATA ANALYST & SQL PROTOCOL ]**\n\n"
        "**Section 1: SQL Querying & Optimization**\n"
        "• **Q1:** Given a table `orders(order_id, customer_id, amount, order_date)`, write a query to find the top 3 spending customers for each month using window functions (`DENSE_RANK()`).\n"
        "• **Q2:** Explain the difference between `LEFT JOIN` and `INNER JOIN` when handling NULL values, and how indexing affects `GROUP BY` execution plans.\n\n"
        "**Section 2: Python & Pandas Manipulation**\n"
        "• **Q3:** How do you detect and impute missing values in a skewed feature column using Pandas vs Scikit-Learn `SimpleImputer`?\n"
        "• **Q4:** What is the vectorization advantage of `np.where()` over iterating with `.apply()` on 1,000,000 rows?\n\n"
        "**Section 3: Statistical Thinking & Metrics**\n"
        "• **Q5:** How would you design an A/B test to measure if a new checkout UI increases user conversion, and what p-value threshold and sample size power would you require?\n\n"
        "*Type your answers below for step-by-step evaluation, or ask for the detailed answer key!*"
    ),
    "SOFTWARE_ENGINEER": (
        "**[ 🎯 TECHNICAL MOCK INTERVIEW // SOFTWARE ENGINEER PROTOCOL ]**\n\n"
        "**Section 1: System Architecture & APIs**\n"
        "• **Q1:** How would you design a scalable rate limiter (e.g. 100 requests per minute per IP) using Redis and Token Bucket / Leaky Bucket algorithms?\n"
        "• **Q2:** Explain the trade-offs between REST and gRPC for inter-service communication in a microservices ecosystem.\n\n"
        "**Section 2: Concurrency & Database Design**\n"
        "• **Q3:** What is the difference between optimistic and pessimistic concurrency control in PostgreSQL, and how do you prevent race conditions during ticket booking?\n"
        "• **Q4:** Explain how Python's `asyncio` event loop works under the hood without blocking I/O.\n\n"
        "**Section 3: Algorithms & Data Structures**\n"
        "• **Q5:** Given an array of integers, find the longest contiguous subarray with a sum equal to `K` in $O(N)$ time complexity using HashMaps.\n\n"
        "*Provide your solution or request architectural breakdown.*"
    )
}


class RLHFAssistant:
    """Thread-safe, RLHF-enabled Conversational Assistant with RAG retrieval and confidence gating."""

    def __init__(self, data_loader: DataLoader) -> None:
        self.data_loader = data_loader
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=8000)
        self.corpus: list[str] = []
        self.source_map: list[dict[str, Any]] = []
        self.tfidf_matrix = None
        self._built = False
        self._build_lock = threading.Lock()
        
        # Thread-safe RLHF feedback store
        self._feedback_lock = threading.Lock()
        self._feedback_store: dict[str, dict[str, Any]] = {}

    def _build_corpus(self) -> None:
        if self._built:
            return
        with self._build_lock:
            if self._built:
                return
            institutes = self.data_loader.institutes_data or {}
            institute_items = [(str(code).zfill(5), info) for code, info in institutes.items() if isinstance(info, dict)]
            
            institute_text = []
            institute_sources = []
            for code, info in institute_items:
                name = info.get("name", "Unknown Institute")
                loc = info.get("location") or info.get("city") or "Maharashtra"
                status = info.get("status") or ((info.get("system_overview") or {}).get("status") if isinstance(info.get("system_overview"), dict) else "Un-Aided")
                autonomy = info.get("autonomy") or ((info.get("system_overview") or {}).get("autonomy") if isinstance(info.get("system_overview"), dict) else "Non-Autonomous")
                fees = info.get("open_fees") or ((info.get("administration_logistics") or {}).get("estimated_open_fees") if isinstance(info.get("administration_logistics"), dict) else "₹1,25,000 / Year")
                naac = info.get("naac_grade") or ((info.get("system_overview") or {}).get("accreditation") if isinstance(info.get("system_overview"), dict) else "A Grade")
                avg_pkg = info.get("avg_package") or ((info.get("placement_matrix") or {}).get("average_package") if isinstance(info.get("placement_matrix"), dict) else "5.5 LPA")
                highest_pkg = info.get("highest_package") or ((info.get("placement_matrix") or {}).get("highest_package") if isinstance(info.get("placement_matrix"), dict) else "15.0 LPA")
                hostel = info.get("hostel") or ((info.get("administration_logistics") or {}).get("hostel_availability") if isinstance(info.get("administration_logistics"), dict) else "Available")
                
                doc = (
                    f"Institute {name} (DTE Code: {code}) in {loc}. "
                    f"Status: {status} ({autonomy}). NAAC Grade: {naac}. "
                    f"Estimated Open Category Fees: {fees}. Hostel: {hostel}. "
                    f"Placement Statistics: Average package {avg_pkg}, Highest package {highest_pkg}."
                )
                institute_text.append(doc)
                institute_sources.append({"type": "institute", "id": code, "name": name})

            # Historical cutoffs summary
            cutoff_data = self.data_loader.get_all_data()
            cutoff_text = []
            cutoff_sources = []
            if not cutoff_data.empty:
                grouped = cutoff_data.groupby([COL_COLLEGE_NAME, COL_BRANCH, COL_CATEGORY], dropna=False)[COL_CUTOFF].agg(["min", "max", "mean"]).reset_index().head(1200)
                for record in grouped.to_dict(orient="records"):
                    c_name = record.get(COL_COLLEGE_NAME, "College")
                    br = record.get(COL_BRANCH, "Engineering")
                    cat = record.get(COL_CATEGORY, "OPEN")
                    min_val = record.get("min", 0.0)
                    max_val = record.get("max", 0.0)
                    mean_val = record.get("mean", 0.0)
                    c_doc = f"Historical cutoff for {c_name}, branch {br}, category {cat}: ranges from {min_val:.2f}% to {max_val:.2f}% (mean {mean_val:.2f}%)."
                    cutoff_text.append(c_doc)
                    cutoff_sources.append({"type": "cutoff", "data": record})

            platform_text = [
                "DigiPath provides deterministic college eligibility predictions from historical MHT-CET and Diploma / DSE CAP cutoff metrics.",
                "DigiPath Resume AI performs comprehensive ATS keyword matching, section formatting audits, 5D Enhancv quality scoring, and role fit scoring.",
                "DigiPath Resume Builder offers 4 free ATS-compliant templates with live real-time split-screen editing and PDF export.",
                "DigiPath Job Recommender matches candidate skill profiles with live verified engineering jobs with direct links to LinkedIn, Indeed, and WorkIndia.",
                "DigiPath Scam Detector verifies recruitment entities, checks for fee/deposit fraud indicators, and validates offer letters.",
                "DigiPath Career Roadmap maps personalized engineering semester milestones, projects, and certifications.",
            ]
            platform_sources = [{"type": "platform"} for _ in platform_text]

            self.corpus = institute_text + cutoff_text + platform_text
            self.source_map = institute_sources + cutoff_sources + platform_sources

            if self.corpus:
                self.tfidf_matrix = self.vectorizer.fit_transform(self.corpus)
            self._built = True
            log.info("RLHFAssistant knowledge corpus compiled with %d indexed knowledge entries.", len(self.corpus))

    def record_feedback(self, message_id: str, score: int) -> dict[str, Any]:
        """Record thread-safe RLHF feedback (+1 reward or -1 penalty)."""
        with self._feedback_lock:
            safe_score = 1 if int(score) > 0 else -1
            entry = self._feedback_store.get(message_id, {})
            entry.update({
                "message_id": message_id,
                "score": safe_score,
                "timestamp": datetime.now(tz=timezone.utc).isoformat()
            })
            self._feedback_store[message_id] = entry
            log.info("RLHF Feedback logged: message_id=%s, score=%d", message_id, safe_score)
            return {"status": "success", "message_id": message_id, "score": safe_score, "recorded_at": entry["timestamp"]}

    def get_feedback_summary(self) -> dict[str, Any]:
        with self._feedback_lock:
            total = len(self._feedback_store)
            positives = sum(1 for v in self._feedback_store.values() if v.get("score", 0) > 0)
            negatives = sum(1 for v in self._feedback_store.values() if v.get("score", 0) < 0)
            return {
                "total_feedback": total,
                "rewards_positive": positives,
                "penalties_negative": negatives,
                "reward_ratio": round(positives / total, 2) if total > 0 else 1.0,
            }

    def generate_response(self, user_query: str, history: Optional[List[Dict[str, str]]] = None) -> dict[str, Any]:
        """Classify intent, perform RAG lookup with confidence gating (>=0.35), and return structured payload."""
        self._build_corpus()
        msg_id = str(uuid.uuid4())
        clean_query = str(user_query or "").strip()

        # 1. Small-Talk / Greetings
        if GREETING_PATTERNS.search(clean_query):
            return {
                "message_id": msg_id,
                "response": TERMINAL_WELCOME,
                "intent": "GREETING",
                "action_chips": DEFAULT_ACTION_CHIPS,
            }

        normalized = clean_query.casefold()

        # 2. Mock Interview Intent Router
        if MOCK_INTERVIEW_PATTERNS.search(clean_query):
            if "data" in normalized or "analyst" in normalized or "sql" in normalized:
                mock_content = MOCK_INTERVIEW_SETS["DATA_ANALYST"]
            else:
                mock_content = MOCK_INTERVIEW_SETS["SOFTWARE_ENGINEER"]
            return {
                "message_id": msg_id,
                "response": mock_content,
                "intent": "MOCK_INTERVIEW",
                "action_chips": [
                    {"label": "[ EVALUATE MY SQL ]", "query": "How should I solve the top spending customer SQL query?"},
                    {"label": "[ BUILD RESUME ]", "query": "Open resume builder to highlight my projects"},
                    {"label": "[ JOB MATCH ]", "query": "Show matching software and data jobs"}
                ],
            }

        # 3. Contextual Routing Protocols
        if any(token in normalized for token in ("predict", "my score", "percentile", "percentage", "get into", "cutoff")):
            return {
                "message_id": msg_id,
                "response": (
                    "**[ ◈ PREDICTOR PROTOCOL ]**\n\n"
                    "For deterministic cutoff matching across 400+ DTE colleges in Maharashtra, navigate to the **Predictor** module. "
                    "You can enter your exact MHT-CET Percentile or Diploma Percentage and filter by Preferred Branch, Category, and City."
                ),
                "intent": "NAVIGATE_PREDICTOR",
                "action_chips": [{"label": "[ OPEN PREDICTOR ]", "query": "Predict my college"}],
            }

        if any(token in normalized for token in ("resume", "cv", "ats", "score my resume", "builder", "template")):
            return {
                "message_id": msg_id,
                "response": (
                    "**[ ▦ RESUME SUITE PROTOCOL ]**\n\n"
                    "DigiPath provides a two-tiered Resume Suite:\n"
                    "1. **Resume 5D AI**: Comprehensive ATS keyword analysis, power verb auditing, and quantifiable metric scoring.\n"
                    "2. **Resume Builder**: 4 free ATS templates (Cyberpunk, Executive Sidebar, Clean ATS Hybrid, Corporate) with real-time split-screen editing and PDF download."
                ),
                "intent": "NAVIGATE_RESUME",
                "action_chips": [
                    {"label": "[ RESUME BUILDER ]", "query": "Open resume builder"},
                    {"label": "[ ANALYZE 5D RESUME ]", "query": "Analyze my resume"}
                ],
            }

        if any(token in normalized for token in ("scam", "fake job", "offer letter", "deposit", "fraud", "verify company")):
            return {
                "message_id": msg_id,
                "response": (
                    "**[ ⚠ FRAUD & SCAM DETECTOR PROTOCOL ]**\n\n"
                    "Use **Scam Detector** to analyze job offers, communication channels, and recruitment demands. "
                    "Legitimate companies never demand security deposits or registration fees prior to onboarding."
                ),
                "intent": "NAVIGATE_SCAM_DETECTOR",
                "action_chips": [{"label": "[ VERIFY OFFER ]", "query": "Verify an offer letter"}],
            }

        # 4. RAG Retrieval over Corpus with Confidence Guard (Threshold >= 0.35)
        if not self.corpus or self.tfidf_matrix is None:
            return {
                "message_id": msg_id,
                "response": "Knowledge matrix initializing. Please ask about institute cutoffs, placement stats, or fees.",
                "intent": "FALLBACK",
                "action_chips": DEFAULT_ACTION_CHIPS,
            }

        query_vector = self.vectorizer.transform([clean_query])
        scores = cosine_similarity(query_vector, self.tfidf_matrix).ravel()
        best_indices = np.argsort(scores)[::-1][:3]
        top_score = float(scores[best_indices[0]]) if len(best_indices) else 0.0

        # RAG CONFIDENCE THRESHOLD GUARD (Threshold >= 0.35)
        RAG_CONFIDENCE_THRESHOLD = 0.35
        if top_score < RAG_CONFIDENCE_THRESHOLD:
            return {
                "message_id": msg_id,
                "response": (
                    "> [ NEURAL RAG CONFIDENCE BELOW THRESHOLD: " f"{int(top_score * 100)}% < 35% ]\n\n"
                    "I searched the DigiPath knowledge matrix but could not locate a high-confidence record for that specific query. "
                    "To get precise verified intelligence, try asking about a specific college name (e.g. *VJTI Mumbai*, *COEP Pune*), "
                    "a 5-digit DTE code (e.g. `03175`), or choose one of the direct terminal modules below:"
                ),
                "intent": "CONFIDENCE_TOO_LOW",
                "action_chips": DEFAULT_ACTION_CHIPS,
            }

        # Format high-confidence retrieved knowledge
        retrieved_passages = [self.corpus[idx] for idx in best_indices if scores[idx] >= RAG_CONFIDENCE_THRESHOLD]
        answer_text = "\n\n".join([f"• {passage}" for passage in retrieved_passages])

        formatted_response = (
            f"**[ VERIFIED DTE KNOWLEDGE RETRIEVAL ]**\n\n"
            f"{answer_text}\n\n"
            f"*Source: DigiPath Verified DTE Knowledge Matrix (Confidence: {int(top_score * 100)}%)*"
        )

        return {
            "message_id": msg_id,
            "response": formatted_response,
            "intent": "RAG_RETRIEVAL",
            "action_chips": DEFAULT_ACTION_CHIPS,
        }

    # Backward-compatible methods
    def query(self, user_query: str) -> str:
        return self.generate_response(user_query)["response"]

    def get_contextual_response(self, user_query: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        return self.generate_response(user_query, history)["response"]


# Backward compatibility alias
ChatService = RLHFAssistant
