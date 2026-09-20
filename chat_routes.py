"""DigiPath Assistant: bounded conversation and explicit local tool routing."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from database import get_db
import models
from chatbot_service import RLHFAssistant
from cet_predictor import CETPredictor, DiplomaPredictor
from data_loader import DataLoader
from scam_service import scam_service
import os

# ---------------------------------------------------------------------------
# Router setup
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/chat", tags=["AI Chat — Intent Router"])
log    = logging.getLogger("digipath.chat_router")

# ---------------------------------------------------------------------------
# Service bootstrap (singleton per process)
# ---------------------------------------------------------------------------

_base_path: str = os.path.dirname(os.path.abspath(__file__))
_data_dir:  str = os.path.join(_base_path, "data")
_inst_json: str = os.path.join(_base_path, "institutes.json")

_loader:       DataLoader    = DataLoader(_data_dir, _inst_json)
_rlhf_service: RLHFAssistant = RLHFAssistant(_loader)
_cet_predictor = CETPredictor(_loader)
_diploma_predictor = DiplomaPredictor(_loader)

# ---------------------------------------------------------------------------
# ── Pydantic Schemas ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class ActionChip(BaseModel):
    """Clickable quick-action button rendered in the chat UI."""
    label: str = Field(..., description="Display label shown to the user.")
    query: str = Field(..., description="Pre-filled query fired when the chip is clicked.")


class ChatRequest(BaseModel):
    """Incoming chat message payload."""
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="User's natural-language query.",
    )
    history_limit: int = Field(
        default=5,
        ge=0,
        le=20,
        description="How many past turns to include as conversational context.",
    )
    conversation: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=12,
        description="Optional client-held current-conversation turns; never treated as instructions.",
    )

    @field_validator("query")
    @classmethod
    def strip_and_validate(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Query must contain non-whitespace characters.")
        return stripped

    @field_validator("conversation")
    @classmethod
    def validate_conversation(cls, turns: list[dict[str, str]]) -> list[dict[str, str]]:
        clean: list[dict[str, str]] = []
        for turn in turns:
            query = str(turn.get("query", "")).strip()[:2000]
            response = str(turn.get("response", "")).strip()[:6000]
            if query and response:
                clean.append({"query": query, "response": response})
        return clean[-12:]


class ChatResponse(BaseModel):
    """Full structured response returned by the intent router."""
    message_id:   str              = Field(..., description="UUID for RLHF feedback tracking.")
    intent:       str              = Field(..., description="Classified intent category.")
    response:     str              = Field(..., description="Formatted markdown response text.")
    action_chips: list[ActionChip] = Field(default_factory=list)
    confidence:   Optional[float]  = Field(None, ge=0.0, le=1.0)
    pipeline:     str              = Field(..., description="Which pipeline handled this request.")
    timestamp:    str              = Field(..., description="ISO-8601 UTC timestamp.")


class FeedbackRequest(BaseModel):
    """Response feedback payload: thumbs up (+1) or thumbs down (-1)."""
    message_id: str = Field(..., min_length=1)
    score:      int = Field(..., description="Must be +1 (positive) or -1 (negative).")

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("Score must be exactly +1 or -1.")
        return v


class FeedbackResponse(BaseModel):
    status:      str   = Field(default="success")
    message_id:  str
    score:       int
    recorded_at: str


class JobApplyRequest(BaseModel):
    """Candidate application payload for the Job Recommender apply endpoint."""
    job_title:    str = Field(..., min_length=2, max_length=255)
    company:      str = Field(..., min_length=2, max_length=255)
    availability: str = Field(
        ...,
        description="One of: 'Immediate', 'In Notice Period', 'Serving Notice'.",
    )
    experience_summary: str = Field(
        ...,
        min_length=30,
        max_length=3000,
        description="Brief experience summary / cover note.",
    )
    portfolio_links: str = Field(
        default="",
        max_length=1000,
        description="Comma-separated or newline-delimited portfolio / GitHub / LinkedIn URLs.",
    )

    @field_validator("availability")
    @classmethod
    def validate_availability(cls, v: str) -> str:
        allowed = {"Immediate", "In Notice Period", "Serving Notice"}
        if v not in allowed:
            raise ValueError(f"availability must be one of {allowed}.")
        return v


class JobApplyResponse(BaseModel):
    status:     str = Field(default="success")
    job_title:  str
    company:    str
    message:    str
    applied_at: str


class HistoryItem(BaseModel):
    id:         int
    query:      str
    response:   str
    created_at: str


# ---------------------------------------------------------------------------
# ── Intent Classification Engine ──────────────────────────────────────────
# ---------------------------------------------------------------------------

# College-admission signals: college names, exam vocab, DTE codes, cutoff terms
_COLLEGE_RAG_PATTERN = re.compile(
    r"""
    \b(
        mht[- ]?cet | dte | cap | fyjc | dse |             # exam names
        coep | vjti | pict | mit[- ]wpu | spit |           # famous MH colleges
        college | institute | cutoff | cut[- ]off |        # generic college vocab
        percentile | percentile[s]? | merit | allotment |  # score terms
        fees?|fee structure|admission|round[- ]?\d |        # process terms
        hostel | naac | accreditation | placement |         # infra terms
        [0-9]{5}                                           # 5-digit DTE code
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# General-tech-mentor signals: anything programming, career, tools, or meta-DigiPath
_GENERAL_MENTOR_PATTERN = re.compile(
    r"""
    \b(
        mock[- ]interview | interview[- ]prep | debug | error |
        code[- ]review | resume | portfolio | roadmap | career |
        hackathon | internship | job[s]? | linkedin | github |
        python | javascript | typescript | react | node | java |
        kotlin | swift | rust | go[- ]lang | django | fastapi |
        sql | nosql | mongodb | redis | kafka | docker | kubernetes |
        aws | gcp | azure | terraform | ci[/]?cd |
        cybersecurity | ctf | penetration | exploit |
        data[- ]science | machine[- ]learning | deep[- ]learning |
        llm | gpt | langchain | transformer | neural[- ]net |
        blockchain | solidity | web3 | smart[- ]contract |
        algorithm | data[- ]structure | system[- ]design |
        what[- ]is | how[- ]to | explain | difference[- ]between |
        help | assist | guide | teach | tips | best[- ]practice
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def classify_intent(query: str) -> str:
    """
    Dual-path intent classifier.

    Returns one of:
      'COLLEGE_ADMISSION_RAG' — route to vector search pipeline
      'GENERAL_TECH_MENTOR'   — route directly to LLM mentor pipeline

    Precedence logic:
      If both patterns match → whichever has more hits wins.
      Tie → COLLEGE_ADMISSION_RAG (domain specificity wins).
    """
    college_hits = len(_COLLEGE_RAG_PATTERN.findall(query))
    mentor_hits  = len(_GENERAL_MENTOR_PATTERN.findall(query))

    if college_hits == 0 and mentor_hits == 0:
        # No strong signal — default to RAG so it can return a graceful fallback
        return "COLLEGE_ADMISSION_RAG"

    if mentor_hits > college_hits:
        return "GENERAL_TECH_MENTOR"

    return "COLLEGE_ADMISSION_RAG"


_SCORE_PATTERN = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*(?:%|percentile)\b", re.IGNORECASE)
_CATEGORY_PATTERN = re.compile(r"\b(OPEN|OBC|SC|ST|EWS|VJNT|SBC)\b", re.IGNORECASE)
_CITY_PATTERN = re.compile(r"\b(Mumbai|Navi Mumbai|Pune|Thane|Nagpur|Nashik|Aurangabad|Kolhapur|Solapur)\b", re.IGNORECASE)


def _assistant_tool(query: str) -> str:
    """Select a capability using an intentionally small, non-privileged allowlist."""
    text = query.casefold()
    if any(word in text for word in ("scam", "fraud", "offer letter", "asking for money", "registration fee")):
        return "SCAM_SCREEN"
    if any(word in text for word in ("resume", "cv", "ats")):
        return "RESUME"
    if any(word in text for word in ("diploma", "dse", "direct second year")):
        return "DIPLOMA_PREDICTOR"
    if ("cet" in text or "percentile" in text) and _SCORE_PATTERN.search(query):
        return "CET_PREDICTOR"
    if "compare" in text and any(word in text for word in ("college", "institute", "vjti", "coep")):
        return "COLLEGE_COMPARISON"
    if any(word in text for word in ("college", "institute", "cutoff", "dte")):
        return "INSTITUTE_RETRIEVAL"
    return "GUIDANCE"


def _prediction_from_query(query: str, diploma: bool) -> dict[str, Any]:
    """Run the existing deterministic predictor only after an explicit score is supplied."""
    score_match = _SCORE_PATTERN.search(query)
    if not score_match:
        label = "diploma percentage" if diploma else "MHT-CET percentile"
        return {
            "intent": "ADMISSION_DETAILS_NEEDED",
            "response": f"I can use DigiPath's {label} tool once you share your score. Include your category and, if useful, branch or city preference.",
            "action_chips": [{"label": "Open Predictor", "query": "Open the predictor"}],
            "confidence": None,
            "pipeline": "TOOL_ROUTING",
        }
    score = float(score_match.group(1))
    if not 0 <= score <= 100:
        return {
            "intent": "INVALID_ADMISSION_SCORE",
            "response": "Please provide a score from 0 to 100.",
            "action_chips": [], "confidence": None, "pipeline": "TOOL_ROUTING",
        }
    category_match, city_match = _CATEGORY_PATTERN.search(query), _CITY_PATTERN.search(query)
    category = category_match.group(1).upper() if category_match else "OPEN"
    city = city_match.group(1).title() if city_match else None
    branch = "Computer" if re.search(r"\b(cse|computer)\b", query, re.I) else None
    try:
        result = (
            _diploma_predictor.predict(percentage=score, category=category, branch=branch, city=city)
            if diploma else _cet_predictor.predict(percentile=score, category=category, branch=branch, city=city)
        )
        records = result.get("results", []) if isinstance(result, dict) else result
        summary = []
        for record in records[:5]:
            summary.append(
                f"- **{record.get('college_name', 'Institute')}** — {record.get('branch', 'Branch')}, "
                f"{record.get('location', 'location unavailable')} · {record.get('classification', 'Estimate')}"
            )
        location_note = " A nearby-city fallback was used." if isinstance(result, dict) and result.get("regional_fallback") else ""
        message = (
            f"Here are data-derived decision-support results for **{score:g}**, **{category}**"
            f"{f', {city}' if city else ''}.{location_note}\n\n"
            + ("\n".join(summary) if summary else "No matching records were found in the bundled dataset for those filters.")
            + "\n\nThese are estimates from bundled historical data, not admission guarantees. Open the Predictor to review filters and the full list."
        )
        return {"intent": "DIPLOMA_PREDICTION" if diploma else "CET_PREDICTION", "response": message,
                "action_chips": [{"label": "Open Predictor", "query": "Open the predictor"}, {"label": "Compare colleges", "query": "Compare the colleges in these results"}],
                "confidence": None, "pipeline": "TOOL_ROUTING"}
    except Exception:
        log.exception("Assistant predictor tool failed")
        return {"intent": "PREDICTOR_UNAVAILABLE", "response": "The predictor is temporarily unavailable. You can still use the Predictor page to try again.",
                "action_chips": [{"label": "Open Predictor", "query": "Open the predictor"}], "confidence": None, "pipeline": "TOOL_ROUTING"}


def _run_assistant_tool(query: str, history: list[dict]) -> Optional[dict[str, Any]]:
    tool = _assistant_tool(query)
    if tool == "CET_PREDICTOR":
        return _prediction_from_query(query, diploma=False)
    if tool == "DIPLOMA_PREDICTOR":
        return _prediction_from_query(query, diploma=True)
    if tool == "SCAM_SCREEN":
        result = scam_service.analyze_text(query)
        signals = result.get("threat_flags", [])[:3]
        return {"intent": "SCAM_SCREEN", "response": f"**{result.get('risk_level', 'SCREENING_RESULT').replace('_', ' ')}**\n\n" + ("\n".join(f"- {item}" for item in signals) if signals else "Share the offer text, sender email, or website for a more useful heuristic screen.") + "\n\nThis is a risk screen, not proof about an employer.", "action_chips": [{"label": "Open Scam Detector", "query": "Open the scam detector"}], "confidence": None, "pipeline": "TOOL_ROUTING"}
    if tool == "RESUME":
        return {"intent": "RESUME_GUIDANCE", "response": "Use the Resume Analyzer to upload a PDF or DOCX for structured feedback. I do not receive or retain an attachment from this chat.", "action_chips": [{"label": "Open Resume Analyzer", "query": "Open the resume analyzer"}], "confidence": None, "pipeline": "TOOL_ROUTING"}
    return None


# ---------------------------------------------------------------------------
# ── General Tech Mentor Pipeline ──────────────────────────────────────────
# ---------------------------------------------------------------------------

# Structured mentor knowledge for GENERAL_TECH_MENTOR intent.
# Expands the existing RLHFAssistant with richer domain coverage.

_MENTOR_RESPONSES: dict[str, tuple[re.Pattern, str, list[dict]]] = {
    "mock_interview": (
        re.compile(
            r"\b(mock[- ]interview|interview[- ]prep|technical[- ]interview|prepare[- ]for[- ]interview)\b",
            re.IGNORECASE,
        ),
        (
            "**[ 🎯 TECHNICAL MOCK INTERVIEW PROTOCOL ]**\n\n"
            "**Section 1 — Data Structures & Algorithms**\n"
            "• **Q1:** Given an unsorted array, find the longest subarray with sum equal to `K` in O(N) using HashMap.\n"
            "• **Q2:** Implement LRU Cache using `OrderedDict` / doubly-linked-list + hash map. Discuss time complexity.\n"
            "• **Q3:** Explain the difference between BFS and DFS. When would you use Dijkstra vs A*?\n\n"
            "**Section 2 — System Design**\n"
            "• **Q4:** Design a URL shortener (bit.ly) that handles 100M writes/day. Detail your DB schema, hashing, and CDN strategy.\n"
            "• **Q5:** How would you implement a rate-limiter supporting 1000 requests/minute/user using Redis Token Bucket?\n\n"
            "**Section 3 — Behavioral (STAR Format)**\n"
            "• **Q6:** Describe a time you resolved a production outage under pressure. What monitoring tools did you use?\n\n"
            "*Reply with your answers for step-by-step evaluation, or type a domain (SQL / Python / System Design) for a specialized set.*"
        ),
        [
            {"label": "[ SQL INTERVIEW SET ]",    "query": "Give me SQL mock interview questions"},
            {"label": "[ PYTHON DEEP DIVE ]",      "query": "Python advanced interview questions"},
            {"label": "[ SYSTEM DESIGN ROUND ]",   "query": "Mock system design interview"},
        ],
    ),
    "sql_interview": (
        re.compile(
            r"\b(sql[- ]interview|sql[- ]questions?|window[- ]function|cte|stored[- ]procedure|query[- ]optimization)\b",
            re.IGNORECASE,
        ),
        (
            "**[ 🗃️ SQL TECHNICAL MOCK INTERVIEW ]**\n\n"
            "**Section 1 — Window Functions**\n"
            "• **Q1:** Write a query to find the top-3 highest-paid employees per department using `DENSE_RANK()` over a partition.\n"
            "• **Q2:** Explain `ROW_NUMBER()` vs `RANK()` vs `DENSE_RANK()` with tie-handling examples.\n\n"
            "**Section 2 — Optimization**\n"
            "• **Q3:** A query with a `GROUP BY` on 50M rows takes 12 seconds. Name 4 strategies to bring it under 500ms.\n"
            "• **Q4:** Explain covering indexes and partial indexes. When does an index hurt performance?\n\n"
            "**Section 3 — Advanced**\n"
            "• **Q5:** Construct a recursive CTE to traverse an employee org-chart hierarchy tree.\n"
            "• **Q6:** What is the difference between `UNION` and `UNION ALL`? How does a hash join differ from a nested loop?\n\n"
            "*Provide your query solutions below for detailed feedback.*"
        ),
        [
            {"label": "[ EVALUATE MY QUERY ]",   "query": "Evaluate my SQL window function solution"},
            {"label": "[ PYTHON DATA SCIENCE ]", "query": "Python and pandas interview questions"},
        ],
    ),
    "resume_review": (
        re.compile(
            r"\b(resume|cv|ats[- ]score|portfolio|linkedin[- ]profile|cover[- ]letter)\b",
            re.IGNORECASE,
        ),
        (
            "**[ ▦ RESUME INTELLIGENCE PROTOCOL ]**\n\n"
            "DigiPath offers a two-tier Resume Suite:\n\n"
            "**1. Resume 5D AI Analyzer** — Upload your PDF/DOCX and receive:\n"
            "   • ATS keyword match score (0–100)\n"
            "   • Power verb and quantification audit\n"
            "   • Skill gap heatmap vs. your target role\n"
            "   • Section-by-section formatting grade\n"
            "   • Career domain classification\n\n"
            "**2. Resume Builder** — 4 ATS-compliant templates:\n"
            "   • Cyberpunk (tech roles)\n"
            "   • Executive Sidebar (leadership)\n"
            "   • Clean ATS Hybrid (universal)\n"
            "   • Corporate (BFSI, consulting)\n"
            "   *Real-time split-screen editing + 1-click PDF export.*\n\n"
            "**Quick Resume Tips:**\n"
            "• Quantify every achievement: `Reduced API latency by 34%` > `Improved performance`\n"
            "• Lead bullets with strong action verbs: Architected, Engineered, Automated, Deployed\n"
            "• Mirror keywords from the JD to pass ATS scanners\n"
            "• Keep to 1 page for < 3 years experience; 2 pages for senior roles"
        ),
        [
            {"label": "[ OPEN RESUME BUILDER ]",  "query": "Open resume builder"},
            {"label": "[ ANALYZE 5D RESUME ]",    "query": "Analyze my resume for ATS score"},
            {"label": "[ JOB MATCH ]",             "query": "Find jobs matching my skills"},
        ],
    ),
    "debugging_help": (
        re.compile(
            r"\b(debug|error|exception|traceback|fix[- ]code|not[- ]working|bug|crash|issue)\b",
            re.IGNORECASE,
        ),
        (
            "**[ 🔧 CODE DEBUGGING PROTOCOL ]**\n\n"
            "To provide targeted help, share:\n"
            "1. **The exact error message** or traceback (copy-paste the full output)\n"
            "2. **The language and framework** (Python/FastAPI, JS/React, etc.)\n"
            "3. **Minimal reproducible code snippet** (the failing function or component)\n"
            "4. **What you expected vs. what actually happened**\n\n"
            "**Common Debug Strategies:**\n"
            "• Python: `import pdb; pdb.set_trace()` or `breakpoint()` for interactive debugging\n"
            "• Async bugs: ensure `await` is not missing on coroutines\n"
            "• FastAPI 422: check Pydantic schema — field names must match the request body exactly\n"
            "• React: open DevTools → Console → check component re-render tree with React DevTools extension\n"
            "• SQL: run `EXPLAIN ANALYZE` on your query to inspect the execution plan\n\n"
            "*Paste your code + error and I'll diagnose it step by step.*"
        ),
        [
            {"label": "[ PASTE YOUR ERROR ]",      "query": "Here is my error: "},
            {"label": "[ PYTHON DEBUGGING ]",      "query": "Debug my Python FastAPI error"},
            {"label": "[ REACT DEBUGGING ]",       "query": "Debug my React component error"},
        ],
    ),
    "cybersecurity": (
        re.compile(
            r"\b(cybersecurity|pentest|ctf|exploit|penetration|owasp|burp[- ]suite|metasploit|xss|sql[- ]injection|vulnerability|capture[- ]the[- ]flag)\b",
            re.IGNORECASE,
        ),
        (
            "**[ 🛡️ CYBERSECURITY INTELLIGENCE PROTOCOL ]**\n\n"
            "**Learning Paths:**\n"
            "• **Beginner:** TryHackMe (free), OverTheWire Bandit, PicoCTF\n"
            "• **Intermediate:** Hack The Box, OWASP WebGoat, VulnHub\n"
            "• **Advanced:** OSCP / eJPT Certification, DEF CON CTF\n\n"
            "**Core Skill Stack:**\n"
            "• **Recon:** Nmap, Shodan, theHarvester, WHOIS\n"
            "• **Web:** Burp Suite, SQLMap, FFUF (fuzzing), Nikto\n"
            "• **Exploitation:** Metasploit, Exploit-DB, custom Python payloads\n"
            "• **Reverse Eng:** Ghidra, Binary Ninja, GDB with PEDA\n"
            "• **CTF Skills:** Steganography, Crypto, Pwn (binary exploit), Web, Forensics\n\n"
            "**⚠️ Ethics Reminder:** Only test systems you own or have explicit written permission for.\n\n"
            "Ask me about a specific CVE, exploit technique, CTF writeup, or certification path."
        ),
        [
            {"label": "[ CTF RESOURCES ]",         "query": "Best CTF platforms for beginners"},
            {"label": "[ OSCP ROADMAP ]",           "query": "How to prepare for OSCP certification"},
            {"label": "[ SCAM DETECTOR ]",          "query": "Verify a suspicious job offer"},
        ],
    ),
    "system_design": (
        re.compile(
            r"\b(system[- ]design|design[- ]pattern|microservice|architecture|load[- ]balan|rate[- ]limit|scalab|database[- ]design|api[- ]design|event[- ]driven|kafka|redis[- ]cache)\b",
            re.IGNORECASE,
        ),
        (
            "**[ 🏗️ SYSTEM DESIGN MENTOR PROTOCOL ]**\n\n"
            "**The RESP Framework (for any design question):**\n"
            "1. **R**equirements Clarification — Functional + Non-functional (scale, latency, consistency)\n"
            "2. **E**stimate Scale — DAU, QPS, storage, bandwidth back-of-envelope\n"
            "3. **S**ystem Components — API Gateway, Load Balancer, App Servers, DB, Cache, CDN, Queue\n"
            "4. **P**oint Out Trade-offs — CAP Theorem: CP vs AP; SQL vs NoSQL; Pull vs Push\n\n"
            "**Key Patterns:**\n"
            "• **Rate Limiting:** Token Bucket (bursty) vs Leaky Bucket (smooth) using Redis INCR + EXPIRE\n"
            "• **Caching:** Cache-aside, Write-through, Write-behind; TTL vs LRU eviction\n"
            "• **DB Scaling:** Vertical → Read Replicas → Sharding → CQRS + Event Sourcing\n"
            "• **Async:** Message queues (Kafka/RabbitMQ) for decoupling, circuit breakers for resilience\n\n"
            "Tell me the specific system you want to design (e.g., Twitter, WhatsApp, Netflix Recommendations)."
        ),
        [
            {"label": "[ DESIGN URL SHORTENER ]",  "query": "Design a URL shortener like bit.ly"},
            {"label": "[ DESIGN TWITTER FEED ]",   "query": "Design Twitter news feed at scale"},
            {"label": "[ MOCK INTERVIEW ]",         "query": "Start system design mock interview"},
        ],
    ),
    "career_guidance": (
        re.compile(
            r"\b(career|roadmap|career[- ]path|what[- ]should[- ]i[- ]learn|where[- ]to[- ]start|become[- ]a|how[- ]to[- ]become|skill[- ]gap|learning[- ]path)\b",
            re.IGNORECASE,
        ),
        (
            "**[ 🧠 CAREER GUIDANCE PROTOCOL ]**\n\n"
            "DigiPath supports 25+ engineering career vectors. Here are the most in-demand in 2026:\n\n"
            "**🔥 Highest Demand:**\n"
            "• **AI/ML Engineer** — LLM fine-tuning, RAG, MLOps → ₹12–40 LPA\n"
            "• **Full Stack Dev** — React + FastAPI/Node + Cloud → ₹6–20 LPA\n"
            "• **DevOps/SRE** — Kubernetes, Terraform, CI/CD → ₹10–28 LPA\n"
            "• **Cybersecurity** — VAPT, SOC, Cloud Security → ₹8–22 LPA\n"
            "• **Data Engineer** — Spark, Airflow, dbt, Snowflake → ₹10–25 LPA\n\n"
            "**📌 Tips for Freshers (0–1 Year):**\n"
            "1. Build 3–5 portfolio projects and host them on GitHub with READMEs\n"
            "2. Contribute to open-source projects (even docs/tests count)\n"
            "3. Get 1 recognized certification (AWS, Google, Microsoft)\n"
            "4. Optimize your LinkedIn profile (500+ connections, open-to-work)\n"
            "5. Apply to 15–20 companies simultaneously — numbers game\n\n"
            "Use the **Neural Roadmap** tool for a phase-by-phase learning plan tailored to your domain."
        ),
        [
            {"label": "[ OPEN NEURAL ROADMAP ]",   "query": "Open career roadmap planner"},
            {"label": "[ JOB MATCHES ]",            "query": "Show jobs matching my target role"},
            {"label": "[ RESUME OPTIMIZER ]",       "query": "Analyze and improve my resume"},
        ],
    ),
    "hackathon_info": (
        re.compile(
            r"\b(hackathon|hack[- ]the[- ]box|ctf|competition|devfolio|unstop|smart[- ]india|sih|coding[- ]contest|competitive[- ]programming)\b",
            re.IGNORECASE,
        ),
        (
            "**[ ⚡ HACKATHON & COMPETITION INTELLIGENCE ]**\n\n"
            "**Tier 1 — National Hackathons (High Impact):**\n"
            "• **Smart India Hackathon (SIH)** — Government-sponsored, ₹1L prize, 36-hour national hackathon\n"
            "• **Flipkart GRID** — e-commerce focus, PPO for winners, 3 rounds\n"
            "• **JP Morgan Code for Good** — 24-hr NGO tech hackathon, PPO offered\n"
            "• **Goldman Sachs Engineering Campus Program** — Algorithmic + full-stack\n\n"
            "**Tier 2 — Community Hackathons:**\n"
            "• **Hack This Fall** (Devfolio) — Open-source & innovation, ₹50K prizes\n"
            "• **HackJNU / HINT / Hack36** — College-level, great for freshers\n\n"
            "**CTF Platforms:**\n"
            "• **PicoCTF** — Best for beginners, Carnegie Mellon backed\n"
            "• **Hack The Box** — Intermediate to advanced, gamified\n"
            "• **CTFtime.org** — Aggregator of all global CTFs with ratings\n\n"
            "**Strategy:** Win 1–2 hackathons and list them prominently on your resume to stand out."
        ),
        [
            {"label": "[ BROWSE DEVFOLIO ]",       "query": "Current hackathons on Devfolio"},
            {"label": "[ CTF BEGINNER GUIDE ]",    "query": "How to start with CTF competitions"},
            {"label": "[ JOB SEARCH ]",             "query": "Show me software engineering jobs"},
        ],
    ),
}

_DEFAULT_MENTOR_CHIPS: list[dict] = [
    {"label": "[ MOCK INTERVIEW ]",     "query": "Start a technical mock interview"},
    {"label": "[ CAREER GUIDANCE ]",   "query": "Help me choose a career path in tech"},
    {"label": "[ HACKATHONS ]",        "query": "Find upcoming hackathons for me"},
    {"label": "[ SYSTEM DESIGN ]",     "query": "Explain system design concepts"},
]

_DEFAULT_RAG_CHIPS: list[dict] = [
    {"label": "[ PREDICT COLLEGE ]",   "query": "Predict my college based on CET percentile"},
    {"label": "[ BUILD RESUME ]",      "query": "Open resume builder to create an ATS resume"},
    {"label": "[ MOCK INTERVIEW ]",    "query": "Start a technical mock interview"},
    {"label": "[ SCAM DETECTOR ]",     "query": "Verify an offer letter or internship for safety"},
]


def _run_general_mentor_pipeline(query: str) -> dict[str, Any]:
    """
    Bypass vector search entirely.
    Match against curated mentor patterns and return a rich structured response.
    Falls back to a helpful generic guidance message — never raises.
    """
    normalized = query.casefold()

    for key, (pattern, response_text, chips) in _MENTOR_RESPONSES.items():
        if pattern.search(query):
            log.info("GENERAL_TECH_MENTOR pipeline matched rule: %s", key)
            return {
                "intent":       f"MENTOR_{key.upper()}",
                "response":     response_text,
                "action_chips": chips,
                "confidence":   None,
                "pipeline":     "GENERAL_TECH_MENTOR",
            }

    # Generic fallback — never raises, always returns a helpful message
    log.info("GENERAL_TECH_MENTOR pipeline: no specific rule matched, returning general guidance.")
    return {
        "intent":   "GENERAL_TECH_MENTOR",
        "response": (
            "**[ 🤖 DIGIPATH AI MENTOR ]**\n\n"
            "I can help you with:\n\n"
            "• **Mock Interviews** — Technical Q&A sets for DSA, SQL, System Design, Python\n"
            "• **Code Debugging** — Share your error + snippet for step-by-step diagnosis\n"
            "• **Career Guidance** — Roadmaps, skill stacks, and salary benchmarks for 25+ domains\n"
            "• **Resume Optimization** — ATS scoring tips and action-verb improvements\n"
            "• **Cybersecurity** — CTF resources, pentest tools, and certification paths\n"
            "• **System Design** — Architecture patterns and the RESP framework\n"
            "• **Hackathons** — Curated national and international opportunities\n\n"
            "Try one of the quick actions below or ask your specific question!"
        ),
        "action_chips": _DEFAULT_MENTOR_CHIPS,
        "confidence":   None,
        "pipeline":     "GENERAL_TECH_MENTOR",
    }


def _run_college_rag_pipeline(query: str, history: list[dict]) -> dict[str, Any]:
    """
    Execute the RAG vector search pipeline via RLHFAssistant.
    On low confidence, falls back to a clean LLM-style guidance message
    WITHOUT throwing an exception or locking the UI.
    """
    try:
        raw = _rlhf_service.generate_response(query, history)
    except Exception as exc:
        # Defensive: catch any internal service error, never propagate to client
        log.error("RLHFAssistant raised an unhandled exception: %s", exc, exc_info=True)
        return {
            "intent":   "RAG_SERVICE_ERROR_RECOVERED",
            "response": (
                "I couldn't retrieve a local answer just now. "
                "Please try again or use one of the direct modules below."
            ),
            "action_chips": _DEFAULT_RAG_CHIPS,
            "confidence":   0.0,
            "pipeline":     "COLLEGE_ADMISSION_RAG",
        }

    intent       = raw.get("intent", "RAG_RETRIEVAL")
    response_txt = raw.get("response", "")
    chips        = raw.get("action_chips", _DEFAULT_RAG_CHIPS)

    # Detect low-confidence fallback from the assistant and enrich it gracefully
    if intent == "CONFIDENCE_TOO_LOW":
        # Re-classify: maybe it should have been a mentor query
        if _GENERAL_MENTOR_PATTERN.search(query):
            log.info("Reclassifying low-confidence RAG query to GENERAL_TECH_MENTOR.")
            return _run_general_mentor_pipeline(query)

        # Otherwise return a clean, non-alarming fallback (no percentage shown to user)
        response_txt = (
            "I couldn't find a precise match in DigiPath's local admission data.\n\n"
            "**Try asking about:**\n"
            "• A specific college name (*VJTI Mumbai*, *COEP Pune*, *MIT Pune*)\n"
            "• A 5-digit DTE code (e.g., `03175`)\n"
            "• A branch + category combo (*Computer Engineering, GOPENS*)\n"
            "• Fees, NAAC grade, or placement stats for a named college\n\n"
            "Or use the **Predictor** tool for a complete ranked list based on your percentile."
        )
        chips = _DEFAULT_RAG_CHIPS

    return {
        "intent":       intent,
        "response":     response_txt,
        "action_chips": chips,
        "confidence":   None,
        "pipeline":     "COLLEGE_ADMISSION_RAG",
    }


# ---------------------------------------------------------------------------
# ── Auth Helper ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

async def _resolve_user_id(request: Request, db: Session) -> Optional[int]:
    """
    Resolve the authenticated user's ID from Authorization header or HTTP cookie.
    Returns None gracefully — never raises — so unauthenticated users still
    get chat responses (history simply won't be persisted).
    """
    try:
        from auth_service import decode_access_token

        # Prefer Authorization header (Bearer token)
        auth_header: str = request.headers.get("Authorization", "")
        token: Optional[str] = None

        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        else:
            # Fall back to cookie
            raw_cookie: str = request.cookies.get("access_token", "")
            if raw_cookie.startswith("Bearer "):
                token = raw_cookie.split(" ", 1)[1].strip()
            elif raw_cookie:
                token = raw_cookie.strip()

        if not token:
            return None

        payload = decode_access_token(token)
        if not payload or not payload.get("sub"):
            return None

        email: str = str(payload["sub"]).strip().lower()
        user = db.query(models.User).filter(models.User.email == email).first()
        return user.id if user else None

    except Exception as exc:
        log.debug("User ID resolution failed (non-critical): %s", exc)
        return None


# ---------------------------------------------------------------------------
# ── Route Handlers ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


def _get_limiter():
    from app import limiter
    return limiter


@router.post(
    "/query",
    response_model=ChatResponse,
    summary="Dual-path intent router",
    description=(
        "Intercepts every query before the vector database. "
        "Classifies intent into COLLEGE_ADMISSION_RAG or GENERAL_TECH_MENTOR "
        "and routes to the appropriate pipeline. "
        "Never raises a 5xx for RAG confidence failures — always falls back cleanly."
    ),
    status_code=status.HTTP_200_OK,
)
async def chat_query(
    body: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Main chat endpoint with dual-path intent classification, rate limiting (20/min), and exception safety."""
    limiter = _get_limiter()
    try:
        await limiter._check_request_limit(
            request,
            endpoint_func=chat_query,
            rate_limit="20/minute",
            is_async=True,
        )
    except Exception:
        pass

    user_id = await _resolve_user_id(request, db)
    if user_id:
        from utils.credits import deduct_user_credits, COST_AI_CHAT
        deduct_user_credits(db, user_id, COST_AI_CHAT)

    # Build bounded context. The browser may send only the current, ephemeral
    # conversation; authenticated history is a separate convenience layer.
    history: list[dict[str, str]] = []
    if user_id and body.history_limit > 0:
        try:
            rows = (
                db.query(models.ChatHistory)
                .filter(models.ChatHistory.user_id == user_id)
                .order_by(models.ChatHistory.created_at.desc())
                .limit(body.history_limit)
                .all()
            )
            history = [{"query": r.query, "response": r.response} for r in reversed(rows)]
        except Exception as exc:
            log.warning("History retrieval failed (non-critical): %s", exc)
    history.extend(body.conversation)
    history = history[-12:]

    # ── Intent Classification ─────────────────────────────────────────
    intent_path = classify_intent(body.query)
    log.info(
        "Intent classified → %s | query_preview=%.80s | user_id=%s",
        intent_path,
        body.query,
        user_id,
    )

    # Explicit feature tools always take precedence over general guidance.
    result = _run_assistant_tool(body.query, history)
    if result is None and intent_path == "GENERAL_TECH_MENTOR":
        result = _run_general_mentor_pipeline(body.query)
    elif result is None:
        result = _run_college_rag_pipeline(body.query, history)

    msg_id    = str(uuid.uuid4())
    timestamp = datetime.now(tz=timezone.utc).isoformat()

    # ── Persist to chat history (best-effort) ──────────────────────────
    if user_id:
        try:
            db.add(models.ChatHistory(
                user_id  = user_id,
                query    = body.query,
                response = result["response"],
            ))
            db.commit()
        except Exception as exc:
            log.warning("Chat history persistence failed (non-critical): %s", exc)
            db.rollback()

    chips = [
        ActionChip(label=c["label"], query=c["query"])
        for c in (result.get("action_chips") or [])
    ]

    return ChatResponse(
        message_id   = msg_id,
        intent       = result["intent"],
        response     = result["response"],
        action_chips = chips,
        confidence   = result.get("confidence"),
        pipeline     = result["pipeline"],
        timestamp    = timestamp,
    )


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="RLHF feedback submission",
    description="Submit +1 (helpful) or -1 (not helpful) feedback on a specific message_id.",
    status_code=status.HTTP_200_OK,
)
async def submit_feedback(body: FeedbackRequest) -> FeedbackResponse:
    """Record RLHF feedback for a specific chat message."""
    try:
        result = _rlhf_service.record_feedback(body.message_id, body.score)
        return FeedbackResponse(
            status      = result.get("status", "success"),
            message_id  = result["message_id"],
            score       = result["score"],
            recorded_at = result["recorded_at"],
        )
    except Exception as exc:
        log.error("RLHF feedback recording failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail      = "Feedback recording failed. Please try again.",
        ) from exc


@router.get(
    "/history",
    response_model=list[HistoryItem],
    summary="User chat history",
    description="Returns the authenticated user's past chat interactions, newest first.",
    status_code=status.HTTP_200_OK,
)
async def get_chat_history(
    request: Request,
    db:      Session = Depends(get_db),
    limit:   int     = 20,
) -> list[HistoryItem]:
    """Retrieve authenticated user's chat history."""
    user_id = await _resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "Authentication required to retrieve chat history.",
        )

    safe_limit = min(max(int(limit), 1), 100)
    try:
        rows = (
            db.query(models.ChatHistory)
            .filter(models.ChatHistory.user_id == user_id)
            .order_by(models.ChatHistory.created_at.desc())
            .limit(safe_limit)
            .all()
        )
        return [
            HistoryItem(
                id         = r.id,
                query      = r.query or "",
                response   = r.response or "",
                created_at = r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]
    except Exception as exc:
        log.error("Chat history retrieval failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail      = "Unable to retrieve chat history.",
        ) from exc


@router.delete("/history", status_code=status.HTTP_200_OK, summary="Clear saved chat history")
async def clear_chat_history(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """Delete only the requesting user's persisted conversations."""
    user_id = await _resolve_user_id(request, db)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    try:
        db.query(models.ChatHistory).filter(models.ChatHistory.user_id == user_id).delete()
        db.commit()
        return {"status": "cleared"}
    except Exception as exc:
        db.rollback()
        log.error("Chat history clear failed: %s", exc)
        raise HTTPException(status_code=500, detail="Unable to clear chat history.") from exc


@router.post(
    "/apply",
    response_model=JobApplyResponse,
    summary="DigiPath job application submission",
    description=(
        "Accepts a candidate's application form payload for a specific job. "
        "Validates all fields with Pydantic before persisting or forwarding."
    ),
    status_code=status.HTTP_200_OK,
)
async def submit_job_application(body: JobApplyRequest) -> JobApplyResponse:
    """
    Job application intake endpoint.
    In production this would email the hiring manager, log to a CRM,
    or forward to a job aggregator API.  Here we validate & acknowledge.
    """
    log.info(
        "Job application received | job=%s | company=%s | availability=%s",
        body.job_title,
        body.company,
        body.availability,
    )
    return JobApplyResponse(
        status     = "success",
        job_title  = body.job_title,
        company    = body.company,
        message    = (
            f"Your application for '{body.job_title}' at {body.company} "
            "has been successfully queued. "
            "Apply directly via the external portals for maximum visibility."
        ),
        applied_at = datetime.now(tz=timezone.utc).isoformat(),
    )


@router.get(
    "/health",
    summary="Chat service health check",
    status_code=status.HTTP_200_OK,
)
async def chat_health() -> dict[str, Any]:
    """Liveness probe for the chat intent router."""
    return {
        "status":           "operational",
        "service":          "DigiPath Chat Intent Router v4.5.0",
        "corpus_built":     _rlhf_service._built,
        "mentor_rules":     len(_MENTOR_RESPONSES),
        "feedback_entries": len(_rlhf_service._feedback_store),
        "timestamp":        datetime.now(tz=timezone.utc).isoformat(),
    }
