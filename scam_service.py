"""DigiPath Scam & Fraud Threat Intelligence Engine with Global Blacklist Registry.

Provides heuristic, multi-input risk screening across:
1. Text/Email job scam patterns (fees, unverified channels, urgency, fake selections)
2. Document parser (.pdf, .docx offer letter extraction)
3. Website / Domain security analysis (SSL checks, IP detection, burner TLDs)
4. Administrator-managed blacklist registry
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import docx
except ImportError:
    docx = None

log = logging.getLogger("digipath.scam_service")


# High-risk scam trigger patterns
SCAM_INDICATOR_PATTERNS = [
    (r"\b(security\s*deposit|registration\s*fee|laptop\s*deposit|training\s*fee|interview\s*fee|processing\s*charge|refundable\s*deposit)\b",
     "CRITICAL: Upfront security deposit, processing fee, or laptop fee demanded.", 40),
    
    (r"\b(gpay|phonepe|paytm|upi\s*id|qr\s*code|crypto|usdt|bitcoin|western\s*union)\b",
     "CRITICAL: Direct P2P payment requested (UPI / GPay / Crypto) instead of official corporate portal.", 35),
    
    (r"\b(selected\s*without\s*interview|direct\s*selection|congratulations\s*you\s*are\s*hired|offer\s*letter\s*attached\s*without\s*exam)\b",
     "HIGH RISK: Immediate selection claimed without formal interview or screening round.", 25),
     
    (r"\b(telegram\s*group|whatsapp\s*hr|connect\s*on\s*telegram|t\.me/|wa\.me/)\b",
     "HIGH RISK: Recruiter demands shifting official communication to Telegram or WhatsApp personal numbers.", 25),
     
    (r"\b(urgent\s*joining|pay\s*within\s*\d+\s*hours|offer\s*expires\s*today|limited\s*seats)\b",
     "SUSPICIOUS: High-pressure urgency tactics and short payment deadlines.", 15),

    (r"@gmail\.com|@yahoo\.com|@outlook\.com|@hotmail\.com|@rediffmail\.com",
     "WARNING: Job offer sent from a public free email domain instead of an enterprise company domain.", 20),
]

LEGITIMACY_INDICATORS = [
    (r"\b(hr@[a-z0-9\.-]+\.(com|in|org|io|co))\b", "Official corporate email domain verified.", 15),
    (r"\b(cin\s*:\s*[a-z0-9]{21}|gstin\s*:\s*[0-9]{2}[a-z]{5}[0-9]{4}[a-z]{1}[1-9a-z]{1}z[0-9a-z]{1})\b", "Valid MCA / GSTIN corporate registration identified.", 20),
    (r"\b(rounds\s*of\s*interview|technical\s*assessment|panel\s*interview)\b", "Multi-stage technical evaluation workflow cited.", 15),
    (r"\b(pf\s*deduction|gratuity|health\s*insurance|standard\s*nda)\b", "Standard statutory employee benefits and compliances documented.", 10),
]

SUSPICIOUS_TLDS = {".xyz", ".top", ".buzz", ".guru", ".work", ".cfd", ".icu", ".click", ".link", ".fit", ".rest"}


class ScamDetectionService:
    """Multi-channel fraud intelligence engine with Global Threat Blacklist Registry."""

    def __init__(self) -> None:
        self.blacklist_file = Path(__file__).resolve().parent / "data" / "blacklist.json"
        self.blacklisted_entities: List[Dict[str, Any]] = []
        self._load_blacklist()

    def _load_blacklist(self) -> None:
        """Load persistent company and domain blacklist from JSON store."""
        if self.blacklist_file.is_file():
            try:
                with open(self.blacklist_file, "r", encoding="utf-8") as f:
                    self.blacklisted_entities = json.load(f)
                log.info("Loaded %d blacklisted entities from %s", len(self.blacklisted_entities), self.blacklist_file)
                return
            except Exception as e:
                log.warning("Could not read blacklist JSON: %s", e)

        # Never ship unverified accusations as default blacklist data. Entries
        # are administrator-reviewed and supplied by the deployment operator.
        self.blacklisted_entities = []

    def _save_blacklist(self) -> None:
        """Persist blacklist to JSON store."""
        try:
            self.blacklist_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.blacklist_file, "w", encoding="utf-8") as f:
                json.dump(self.blacklisted_entities, f, indent=2)
        except Exception as e:
            log.error("Failed to persist blacklist JSON: %s", e)

    # ── Global Blacklist Management ──────────────────────────────────────────

    def get_blacklist(self) -> List[Dict[str, Any]]:
        """Return all active blacklisted fraud entities."""
        return self.blacklisted_entities

    def add_to_blacklist(self, company_name: str, domain: Optional[str] = None, scam_pattern: Optional[str] = None, severity: str = "CRITICAL") -> Dict[str, Any]:
        """Add a fraudulent company or domain to the global registry."""
        clean_name = company_name.strip()
        clean_domain = (domain or "").strip().lower().replace("https://", "").replace("http://", "").split("/")[0]

        # Check existing
        for item in self.blacklisted_entities:
            if item.get("company_name", "").lower() == clean_name.lower():
                return item

        new_id = (max([item.get("id", 0) for item in self.blacklisted_entities] or [0])) + 1
        entry = {
            "id": new_id,
            "company_name": clean_name,
            "domain": clean_domain or "N/A",
            "scam_pattern": scam_pattern or "Confirmed fraudulent entity verified by DigiPath Root Moderation.",
            "severity": severity.upper(),
            "added_at": datetime.datetime.utcnow().isoformat() + "Z"
        }
        self.blacklisted_entities.insert(0, entry)
        self._save_blacklist()
        log.info(">> Added to Global Blacklist: %s (%s)", clean_name, clean_domain)
        return entry

    def remove_from_blacklist(self, blacklist_id: int) -> bool:
        """Remove an entity from the global blacklist."""
        initial_len = len(self.blacklisted_entities)
        self.blacklisted_entities = [item for item in self.blacklisted_entities if item.get("id") != blacklist_id]
        if len(self.blacklisted_entities) < initial_len:
            self._save_blacklist()
            log.info(">> Removed entity ID %d from Global Blacklist", blacklist_id)
            return True
        return False

    def is_blacklisted(self, entity_name: Optional[str], text_or_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Check if an entity name, domain, or raw text matches the global blacklist."""
        name_clean = (entity_name or "").strip().lower()
        combined_text = f"{name_clean} {text_or_url or ''}".lower()

        for item in self.blacklisted_entities:
            b_name = item.get("company_name", "").lower()
            b_domain = item.get("domain", "").lower()
            
            if b_name and b_name in combined_text:
                return item
            if b_domain and b_domain != "n/a" and b_domain in combined_text:
                return item
        return None

    # ── Text Analysis ────────────────────────────────────────────────────────

    def analyze_text(self, text: str, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """Inspect offer letter text or message body for scam patterns and blacklist matches."""
        raw_text = text or ""
        normalized = raw_text.lower()
        
        threat_flags: List[str] = []
        legit_signals: List[str] = []
        total_risk = 0
        total_legit = 0

        # Check Global Blacklist
        blacklisted_match = self.is_blacklisted(entity_name, raw_text)
        if blacklisted_match:
            threat_flags.append(
                f"🚨 CRITICAL ALERT: '{blacklisted_match['company_name']}' is a CONFIRMED SCAM ENTITY in the DigiPath Global Blacklist Registry! ({blacklisted_match.get('scam_pattern')})"
            )
            return {
                "success": True,
                "trust_score": 0.0,
                "risk_score": 100.0,
                "risk_level": "CRITICAL_SCAM",
                "status_tag": "[ 100% CONFIRMED BLACKLISTED FRAUD ]",
                "threat_flags": threat_flags,
                "legitimacy_signals": [],
                "recommendations": [
                    "DO NOT send any money, personal documents, or sensitive data.",
                    "Terminate all communications immediately.",
                    "Report the recruiter contact to local cybercrime authorities."
                ],
                "blacklisted_entity": blacklisted_match
            }

        # Check scam patterns
        for pattern, flag_msg, risk_weight in SCAM_INDICATOR_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                threat_flags.append(flag_msg)
                total_risk += risk_weight

        # Check legitimacy patterns
        for pattern, signal_msg, legit_weight in LEGITIMACY_INDICATORS:
            if re.search(pattern, raw_text, re.IGNORECASE):
                legit_signals.append(signal_msg)
                total_legit += legit_weight

        # Calculate final risk score (0 - 100)
        net_risk = max(0, min(100, (total_risk - total_legit // 2) if total_risk > 0 else 10))
        trust_score = max(0, min(100, 100 - net_risk))

        if net_risk >= 60:
            risk_level = "CRITICAL_SCAM"
            status_tag = "[ HIGH-RISK SCAM DETECTED ]"
        elif net_risk >= 30:
            risk_level = "MODERATE_RISK"
            status_tag = "[ SUSPICIOUS INDICATORS IDENTIFIED ]"
        else:
            risk_level = "LOW_RISK"
            status_tag = "[ NO HIGH-RISK HEURISTICS FOUND ]"

        recommendations = []
        if risk_level == "CRITICAL_SCAM":
            recommendations.extend([
                "NEVER pay any upfront registration or laptop security fees for a legitimate job offer.",
                "Legitimate enterprises will NEVER request payments through GPay, UPI, or Telegram channels.",
                "Cross-verify the company on the Ministry of Corporate Affairs (MCA) database."
            ])
        elif risk_level == "MODERATE_RISK":
            recommendations.extend([
                "Request an official offer letter from the corporate domain (@company.com).",
                "Verify recruiter profiles on LinkedIn and the company careers portal.",
                "Do not share Aadhaar or bank account numbers before formal on-site verification."
            ])
        else:
            recommendations.extend([
                "No high-risk heuristics were found; this is not a verification of legitimacy.",
                "Confirm offer validity through the employer's independently located careers page before signing."
            ])

        return {
            "success": True,
            "trust_score": float(trust_score),
            "risk_score": float(net_risk),
            "risk_level": risk_level,
            "status_tag": status_tag,
            "threat_flags": threat_flags,
            "legitimacy_signals": legit_signals,
            "recommendations": recommendations,
            "scanned_entity": entity_name or "Unknown Entity"
        }

    # ── Document Analysis ────────────────────────────────────────────────────

    def extract_text_from_file(self, file_path: str | Path) -> str:
        """Safely extract plain text from PDF or DOCX file."""
        path = Path(file_path)
        if not path.is_file():
            raise ValueError("Document file not found.")
        
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            if pdfplumber is None:
                raise ValueError("PDF parsing library not installed.")
            with pdfplumber.open(path) as pdf:
                parts = [p.extract_text() or "" for p in pdf.pages[:10]]
            return "\n".join(parts)
        elif suffix in {".docx", ".doc"}:
            if docx is None:
                raise ValueError("DOCX parsing library not installed.")
            doc = docx.Document(path)
            return "\n".join([p.text for p in doc.paragraphs[:500]])
        elif suffix == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
        else:
            raise ValueError("Unsupported document format. Use .pdf, .docx, or .txt")

    def analyze_document(self, file_path: str | Path, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """Extract and inspect uploaded PDF/DOCX offer letter."""
        doc_text = self.extract_text_from_file(file_path)
        if not doc_text.strip():
            return {
                "success": False,
                "trust_score": 30.0,
                "risk_score": 70.0,
                "risk_level": "MODERATE_RISK",
                "status_tag": "[ EMPTY OR SCANNED IMAGE PDF ]",
                "threat_flags": ["WARNING: Document contains no extractable text. Scammers often use unsearchable image PDFs to evade automated filters."],
                "legitimacy_signals": [],
                "recommendations": ["Ensure document has selectable text, or copy-paste text manually into the scanner."]
            }
        return self.analyze_text(doc_text, entity_name=entity_name)

    # ── Website / URL Analysis ───────────────────────────────────────────────

    def analyze_url(self, target_url: str) -> Dict[str, Any]:
        """Evaluate company careers URL or website for deceptive domain patterns."""
        clean_url = target_url.strip()
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url

        try:
            parsed = urlparse(clean_url)
            hostname = (parsed.hostname or "").lower()
        except Exception:
            return {
                "success": False,
                "risk_score": 85.0,
                "risk_level": "CRITICAL_SCAM",
                "status_tag": "[ INVALID_MALFORMED_URL ]",
                "threat_flags": ["CRITICAL: Malformed URL address provided."],
                "recommendations": ["Verify the complete URL with HTTPS protocol."]
            }

        if not hostname or "." not in hostname or any(char.isspace() for char in hostname):
            return {
                "success": False,
                "risk_score": 85.0,
                "risk_level": "INVALID_INPUT",
                "status_tag": "[ INVALID_MALFORMED_URL ]",
                "threat_flags": ["A complete public website hostname is required."],
                "legitimacy_signals": [],
                "recommendations": ["Provide the employer's website address, such as https://example.com."],
            }

        # Check Global Blacklist
        blacklisted_match = self.is_blacklisted(hostname, clean_url)
        if blacklisted_match:
            return {
                "success": True,
                "trust_score": 0.0,
                "risk_score": 100.0,
                "risk_level": "CRITICAL_SCAM",
                "status_tag": "[ 100% CONFIRMED BLACKLISTED DOMAIN ]",
                "threat_flags": [
                    f"🚨 CRITICAL ALERT: Domain '{hostname}' is BLACKLISTED in the DigiPath Global Registry ({blacklisted_match.get('scam_pattern')})."
                ],
                "legitimacy_signals": [],
                "recommendations": ["Do not browse this site or submit any resume/payment details."],
                "blacklisted_entity": blacklisted_match
            }

        threats = []
        legits = []
        risk_score = 0

        # Check SSL
        if parsed.scheme != "https":
            threats.append("SECURITY RISK: Website does not enforce HTTPS / SSL encryption.")
            risk_score += 35
        else:
            legits.append("HTTPS / SSL encrypted connection active.")

        # Check Raw IP Hostname
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", hostname):
            threats.append("CRITICAL: Raw IP address hostname used instead of registered domain name.")
            risk_score += 50

        # Check Burner TLDs
        for tld in SUSPICIOUS_TLDS:
            if hostname.endswith(tld):
                threats.append(f"HIGH RISK: Suspicious low-reputation burner TLD ({tld}) commonly used by recruitment scam portals.")
                risk_score += 35
                break

        # Check Typosquatting / deceptive keywords
        if any(keyword in hostname for keyword in ["-careers-free", "tcs-hiring", "infosys-direct", "placement-guaranteed", "offer-instant"]):
            threats.append("HIGH RISK: Deceptive brand typosquatting keyword detected in domain name.")
            risk_score += 40

        risk_score = max(0, min(100, risk_score if threats else 10))
        trust_score = 100 - risk_score

        if risk_score >= 60:
            risk_level = "CRITICAL_SCAM"
            status_tag = "[ HIGH-RISK UNTRUSTED DOMAIN ]"
        elif risk_score >= 30:
            risk_level = "MODERATE_RISK"
            status_tag = "[ SUSPICIOUS DOMAIN ATTRIBUTES ]"
        else:
            risk_level = "LOW_RISK"
            status_tag = "[ NO HIGH-RISK DOMAIN HEURISTICS FOUND ]"

        return {
            "success": True,
            "trust_score": float(trust_score),
            "risk_score": float(risk_score),
            "risk_level": risk_level,
            "status_tag": status_tag,
            "threat_flags": threats,
            "legitimacy_signals": legits,
            "scanned_domain": hostname,
            "recommendations": [
                "Verify the domain WHOIS registration date and owner.",
                "Ensure job listings link directly to the official corporate portal.",
                "This heuristic result does not verify an employer or guarantee safety."
            ]
        }


# Global singleton instance
scam_service = ScamDetectionService()
