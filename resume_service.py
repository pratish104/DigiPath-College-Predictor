"""DigiPath 5D Engine & ATS Suite.

Provides deterministic, offline-safe resume parsing, 5-dimensional ATS scoring,
safe matrix shape alignment, and targeted job description tailoring.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import docx
except ImportError:
    docx = None

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_DOCX_PARAGRAPHS = 4000
MAX_EXTRACTED_CHARACTERS = 250_000


class DocumentValidationError(ValueError):
    """Raised when an uploaded document cannot be safely analyzed."""


ROLE_SKILLS: Mapping[str, tuple[str, ...]] = {
    "Backend Engineer": ("python", "fastapi", "django", "flask", "sql", "postgresql", "mysql", "api", "docker", "git", "redis", "microservices"),
    "Full Stack Developer": ("python", "javascript", "typescript", "react", "node js", "html", "css", "sql", "docker", "next js", "mongodb"),
    "Data Analyst": ("python", "sql", "excel", "power bi", "tableau", "pandas", "numpy", "statistics", "data visualization", "etl"),
    "ML Engineer": ("python", "machine learning", "scikit learn", "tensorflow", "pytorch", "pandas", "numpy", "mlops", "deep learning", "nlp"),
    "DevOps Engineer": ("docker", "kubernetes", "aws", "linux", "ci cd", "jenkins", "terraform", "git", "ansible", "prometheus"),
    "Cybersecurity Analyst": ("network security", "owasp", "siem", "wireshark", "incident response", "python", "linux", "cryptography", "firewalls"),
}

SKILL_VOCABULARY = tuple(sorted({skill for skills in ROLE_SKILLS.values() for skill in skills}, key=str.casefold))

# Weak action verbs to detect & replacements
WEAK_VERBS: Mapping[str, str] = {
    "worked on": "engineered / spearheaded",
    "handled": "orchestrated / executed",
    "assisted": "collaborated / co-developed",
    "responsible for": "led / implemented",
    "helped with": "facilitated / delivered",
    "did": "developed / architected",
    "made": "constructed / designed",
    "tried": "initiated / deployed",
    "looked at": "analyzed / evaluated",
    "talked to": "negotiated / liaised with",
    "supported": "optimized / maintained",
}

STRONG_ACTION_VERBS: tuple[str, ...] = (
    "engineered", "architected", "orchestrated", "spearheaded", "developed",
    "deployed", "optimized", "streamlined", "implemented", "designed",
    "accelerated", "automated", "built", "scaled", "formulated", "executed",
    "reduced", "increased", "maximized", "integrated", "transformed"
)

SECTION_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "Contact Info": re.compile(r"(\b[\w\.-]+@[\w\.-]+\.\w+\b|\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b|linkedin\.com|github\.com)", re.IGNORECASE),
    "Summary / Objective": re.compile(r"(summary|objective|profile|about\s+me|professional\s+summary)", re.IGNORECASE),
    "Technical Skills": re.compile(r"(technical\s+skills|skills|technologies|tools|competencies|tech\s+stack)", re.IGNORECASE),
    "Work Experience / Projects": re.compile(r"(experience|employment|work\s+history|projects|academic\s+projects|professional\s+experience)", re.IGNORECASE),
    "Education": re.compile(r"(education|academic\s+background|qualifications|degree|bachelor|diploma|university|college)", re.IGNORECASE),
    "Certifications": re.compile(r"(certifications|certificates|licenses|courses|accreditations)", re.IGNORECASE),
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _keyword_pattern(skill: str) -> re.Pattern[str]:
    token_pattern = r"\s+".join(re.escape(token) for token in skill.split())
    return re.compile(rf"(?<![a-z0-9]){token_pattern}(?![a-z0-9])", flags=re.IGNORECASE)


def safe_cosine_similarity(vec_a: Any, vec_b: Any) -> float:
    """Safe cosine similarity calculation with guaranteed 2D matrix shape alignment."""
    try:
        if hasattr(vec_a, "toarray"):
            vec_a = vec_a.toarray()
        if hasattr(vec_b, "toarray"):
            vec_b = vec_b.toarray()

        vec_a = np.asarray(vec_a, dtype=np.float64)
        vec_b = np.asarray(vec_b, dtype=np.float64)

        if vec_a.ndim == 1:
            vec_a = vec_a.reshape(1, -1)
        elif vec_a.ndim > 1 and vec_a.shape[0] > 1:
            vec_a = np.mean(vec_a, axis=0, keepdims=True)

        if vec_b.ndim == 1:
            vec_b = vec_b.reshape(1, -1)
        elif vec_b.ndim > 1 and vec_b.shape[0] > 1:
            vec_b = np.mean(vec_b, axis=0, keepdims=True)

        if vec_a.shape[1] != vec_b.shape[1]:
            return 0.0

        sim_score = float(cosine_similarity(vec_a, vec_b)[0][0])
        if np.isnan(sim_score) or np.isinf(sim_score):
            return 0.0
        return float(np.clip(sim_score, 0.0, 1.0))
    except Exception:
        return 0.0


class ResumeService:
    """DigiPath 5D Engine for ATS analysis, matrix shape safety, and job tailoring."""

    def __init__(self) -> None:
        self.role_names = tuple(ROLE_SKILLS.keys())
        self.role_documents = tuple(" ".join(ROLE_SKILLS[role]) for role in self.role_names)
        self.vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2), sublinear_tf=True)
        self.role_matrix = self.vectorizer.fit_transform(self.role_documents)
        self.skill_patterns = {skill: _keyword_pattern(skill) for skill in SKILL_VOCABULARY}
        self.count_vectorizer = CountVectorizer(
            vocabulary={skill: index for index, skill in enumerate(SKILL_VOCABULARY)},
            binary=True,
            lowercase=True
        )

    def extract_text(self, file_path: str | Path) -> str:
        path = Path(file_path)
        if not path.is_file():
            raise DocumentValidationError("Uploaded document was not found.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise DocumentValidationError("Document exceeds the 10 MB upload limit.")
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            return self._extract_pdf_text(path)
        if suffix == ".docx":
            return self._extract_docx_text(path)
        raise DocumentValidationError("Only PDF and DOCX resumes are supported.")

    def _extract_pdf_text(self, path: Path) -> str:
        if pdfplumber is None:
            raise DocumentValidationError("PDF processing is unavailable on this server.")
        try:
            with pdfplumber.open(path) as document:
                if len(document.pages) > MAX_PDF_PAGES:
                    raise DocumentValidationError("PDF exceeds the 20-page analysis limit.")
                parts = [(page.extract_text() or "") for page in document.pages]
        except DocumentValidationError:
            raise
        except Exception as exc:
            raise DocumentValidationError("PDF could not be read safely.") from exc
        text = "\n".join(parts)
        if len(text) > MAX_EXTRACTED_CHARACTERS:
            raise DocumentValidationError("PDF text exceeds the analysis limit.")
        if not text.strip():
            raise DocumentValidationError("PDF contains no extractable text.")
        return text

    def _extract_docx_text(self, path: Path) -> str:
        if docx is None:
            raise DocumentValidationError("DOCX processing is unavailable on this server.")
        try:
            document = docx.Document(path)
            paragraphs = [paragraph.text for paragraph in document.paragraphs[:MAX_DOCX_PARAGRAPHS]]
            if len(document.paragraphs) > MAX_DOCX_PARAGRAPHS:
                raise DocumentValidationError("DOCX exceeds the document-element analysis limit.")
        except DocumentValidationError:
            raise
        except Exception as exc:
            raise DocumentValidationError("DOCX could not be read safely.") from exc
        text = "\n".join(paragraphs)
        if len(text) > MAX_EXTRACTED_CHARACTERS:
            raise DocumentValidationError("DOCX text exceeds the analysis limit.")
        if not text.strip():
            raise DocumentValidationError("DOCX contains no extractable text.")
        return text

    def extract_skills(self, text: str) -> List[str]:
        normalized = _normalize_text(text)
        return [skill for skill in SKILL_VOCABULARY if self.skill_patterns[skill].search(normalized)]

    def _role_scores(self, text: str) -> np.ndarray:
        try:
            resume_vector = self.vectorizer.transform([text])
            r_vec = resume_vector.toarray() if hasattr(resume_vector, "toarray") else np.asarray(resume_vector)
            m_vec = self.role_matrix.toarray() if hasattr(self.role_matrix, "toarray") else np.asarray(self.role_matrix)

            scores = []
            for i in range(m_vec.shape[0]):
                sim = safe_cosine_similarity(r_vec, m_vec[i:i+1])
                scores.append(sim)
            return np.array(scores, dtype=np.float64)
        except Exception:
            scores = []
            normalized = _normalize_text(text)
            for role, skills in ROLE_SKILLS.items():
                matched = sum(1 for s in skills if self.skill_patterns.get(s, _keyword_pattern(s)).search(normalized))
                scores.append(matched / len(skills) if skills else 0.0)
            return np.array(scores, dtype=np.float64)

    def _recommend_roles(self, scores: np.ndarray) -> List[str]:
        ordering = np.argsort(-scores, kind="stable")
        return [self.role_names[index] for index in ordering[:3] if scores[index] > 0.0]

    # ── DigiPath 5-Dimensional Quality Scoring Engine ────────────────────────

    def evaluate_5d(self, text: str, skills: List[str], target_role: Optional[str], role_scores: np.ndarray) -> Dict[str, Any]:
        """Calculates granular DigiPath 5D scores across 5 core quality dimensions."""

        # 1. Dimension 1: Impact & Quantifiable Metrics (30% weight)
        metric_patterns = [
            r"\b\d+(\.\d+)?%\b",
            r"[\$₹€£]\s*\d+([,\.]\d+)*(\s*(k|m|cr|lakh|million|lpa))?",
            r"\b(reduced|increased|boosted|improved|saved|optimized|scaled|accelerated)\b[^\.\n]*?\b\d+",
            r"\b\d+\s*(ms|seconds|minutes|hours|days|x|users|clients|requests|qps|stars|downloads|team members|developers)\b",
            r"\b(top\s*\d+%|rank\s*\d+|1st|2nd|3rd)\b"
        ]
        impact_matches = []
        for pattern in metric_patterns:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            impact_matches.extend(matches)

        impact_count = len(impact_matches)
        if impact_count >= 5:
            impact_score = 100
        elif impact_count >= 3:
            impact_score = 80
        elif impact_count >= 1:
            impact_score = 55
        else:
            impact_score = 25

        # 2. Dimension 2: Action Power Verbs (20% weight)
        normalized = text.casefold()
        weak_verbs_found = []
        for weak_verb, fix in WEAK_VERBS.items():
            if re.search(rf"\b{re.escape(weak_verb)}\b", normalized):
                weak_verbs_found.append({"found": weak_verb, "recommendation": fix})

        strong_verbs_count = sum(1 for verb in STRONG_ACTION_VERBS if re.search(rf"\b{re.escape(verb)}\b", normalized))
        base_verb_score = min(strong_verbs_count * 15, 100)
        penalty = len(weak_verbs_found) * 10
        action_verb_score = int(np.clip(base_verb_score - penalty + (20 if strong_verbs_count >= 3 else 0), 10, 100))

        # 3. Dimension 3: ATS Keyword Match & Density (25% weight)
        selected_role = target_role if target_role in ROLE_SKILLS else (self.role_names[int(np.argmax(role_scores))] if len(role_scores) else self.role_names[0])
        target_skills = ROLE_SKILLS[selected_role]
        matched_role_skills = [s for s in target_skills if s in skills]
        coverage_ratio = len(matched_role_skills) / len(target_skills) if target_skills else 0.0

        semantic_sim = float(role_scores[self.role_names.index(selected_role)]) if selected_role in self.role_names and len(role_scores) else 0.0
        ats_keyword_score = int(round(np.clip((coverage_ratio * 0.7 + semantic_sim * 0.3) * 100.0, 0.0, 100.0)))

        # 4. Dimension 4: Structure & Completeness (15% weight)
        sections_found = {}
        missing_sections = []
        for sec_name, pattern in SECTION_PATTERNS.items():
            found = bool(pattern.search(text))
            sections_found[sec_name] = found
            if not found:
                missing_sections.append(sec_name)

        structure_score = int(round((len([v for v in sections_found.values() if v]) / len(SECTION_PATTERNS)) * 100))

        # 5. Dimension 5: Style, Brevity & Repetition (10% weight)
        sentences = [s.strip() for s in re.split(r"[.\n]+", text) if s.strip()]
        long_sentences = [s for s in sentences if len(s.split()) > 30]
        words = [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", normalized) if w not in {"the", "and", "for", "with", "this", "that"}]
        word_freq: Dict[str, int] = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1

        repetitive_words = [w for w, count in word_freq.items() if count > 6 and w not in SKILL_VOCABULARY]

        style_score = 100
        if len(long_sentences) > 3:
            style_score -= 25
        elif len(long_sentences) > 0:
            style_score -= 10

        if len(repetitive_words) > 3:
            style_score -= 20
        elif len(repetitive_words) > 0:
            style_score -= 10

        style_score = int(np.clip(style_score, 20, 100))

        overall_5d = int(round(
            (impact_score * 0.30) +
            (action_verb_score * 0.20) +
            (ats_keyword_score * 0.25) +
            (structure_score * 0.15) +
            (style_score * 0.10)
        ))

        return {
            "overall_5d_score": overall_5d,
            "dimensions": {
                "impact": {
                    "score": impact_score,
                    "weight": 30,
                    "metrics_detected": impact_count,
                    "label": "Quantifiable Metrics & Impact"
                },
                "action_verbs": {
                    "score": action_verb_score,
                    "weight": 20,
                    "strong_verbs_count": strong_verbs_count,
                    "weak_verbs_found": weak_verbs_found,
                    "label": "Power Verbs & Tone"
                },
                "ats_keywords": {
                    "score": ats_keyword_score,
                    "weight": 25,
                    "matched_count": len(matched_role_skills),
                    "target_count": len(target_skills),
                    "label": "Technical ATS Keywords"
                },
                "structure": {
                    "score": structure_score,
                    "weight": 15,
                    "sections_present": sections_found,
                    "missing_sections": missing_sections,
                    "label": "Structure & Completeness"
                },
                "style": {
                    "score": style_score,
                    "weight": 10,
                    "long_sentences_count": len(long_sentences),
                    "repetitive_words": repetitive_words[:5],
                    "label": "Brevity & Readability"
                }
            }
        }

    # ── Job Tailoring Engine ─────────────────────────────────────────────────

    def tailor_resume(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        """Compares resume against a target job description, generating exact match metrics and bullet rewrites."""
        norm_resume = _normalize_text(resume_text)
        norm_jd = _normalize_text(job_description)

        jd_skills = [skill for skill in SKILL_VOCABULARY if self.skill_patterns[skill].search(norm_jd)]
        resume_skills = [skill for skill in SKILL_VOCABULARY if self.skill_patterns[skill].search(norm_resume)]

        matching_skills = [s for s in jd_skills if s in resume_skills]
        missing_skills = [s for s in jd_skills if s not in resume_skills]

        vec_r = self.vectorizer.transform([norm_resume])
        vec_j = self.vectorizer.transform([norm_jd])
        semantic_match = safe_cosine_similarity(vec_r, vec_j)

        skill_coverage_pct = round((len(matching_skills) / len(jd_skills) * 100.0) if jd_skills else (semantic_match * 100.0), 1)
        tailored_ats_score = int(round(np.clip((skill_coverage_pct * 0.7 + (semantic_match * 100.0) * 0.3), 0.0, 100.0)))

        bullet_rewrites = []
        if missing_skills:
            top_missing = missing_skills[:4]
            bullet_rewrites.append({
                "original_bullet": "Developed backend features and handled database queries.",
                "optimized_bullet": f"Engineered scalable microservices utilizing {top_missing[0].capitalize()} and optimized SQL indexing, improving response latency by 28%.",
                "reason": f"Injects missing high-priority target keyword '{top_missing[0]}' and quantifiable impact."
            })
            if len(top_missing) > 1:
                bullet_rewrites.append({
                    "original_bullet": "Worked on deployment and cloud servers.",
                    "optimized_bullet": f"Orchestrated automated CI/CD pipelines deploying {top_missing[1].capitalize()} containers, achieving 99.9% uptime SLA.",
                    "reason": f"Replaces weak verb 'worked on' with 'orchestrated' and adds '{top_missing[1]}' proficiency."
                })
        else:
            bullet_rewrites.append({
                "original_bullet": "Responsible for managing project workflows and code quality.",
                "optimized_bullet": "Spearheaded Agile sprint lifecycles and automated unit test suites, boosting code coverage from 62% to 94%.",
                "reason": "Quantifies delivery metrics and highlights technical leadership."
            })

        return {
            "tailored_ats_score": tailored_ats_score,
            "skill_coverage_pct": skill_coverage_pct,
            "semantic_similarity_pct": round(semantic_match * 100.0, 1),
            "matching_skills": matching_skills,
            "missing_skills": missing_skills,
            "bullet_rewrites": bullet_rewrites,
            "summary_recommendation": (
                f"Your resume matches {skill_coverage_pct}% of the target job description requirements. "
                + (f"Adding evidence of [{', '.join(missing_skills[:3])}] will significantly improve recruiter screening match." if missing_skills else "Great alignment with target requirements!")
            )
        }

    def _suggestions(self, skills: List[str], recommended_roles: List[str], ats_score: int, evaluation_5d: Dict[str, Any]) -> List[str]:
        primary_role = recommended_roles[0] if recommended_roles else self.role_names[0]
        missing = [skill for skill in ROLE_SKILLS[primary_role] if skill not in skills]
        suggestions = [f"Add verified evidence of {skill} for {primary_role} roles." for skill in missing[:3]]

        dims = evaluation_5d.get("dimensions", {})
        if dims.get("impact", {}).get("score", 100) < 60:
            suggestions.append("Add measurable outcomes (%, $, latency, team scale) to your project and experience bullet points.")
        if dims.get("action_verbs", {}).get("weak_verbs_found"):
            weak_v = dims["action_verbs"]["weak_verbs_found"][0]
            suggestions.append(f"Replace weak verb '{weak_v['found']}' with strong action verbs like '{weak_v['recommendation']}'.")
        if dims.get("structure", {}).get("missing_sections"):
            missing_sec = dims["structure"]["missing_sections"][0]
            suggestions.append(f"Include a dedicated '{missing_sec}' section to pass strict enterprise ATS parsers.")
        if not skills:
            suggestions.append("Add a clearly labeled Technical Skills section formatted with role-specific keywords.")
        return suggestions

    def analyze(self, file_path: str | Path, interests: Optional[List[str]] = None, target_role: Optional[str] = None) -> Dict[str, Any]:
        raw_text = self.extract_text(file_path)
        skills = self.extract_skills(raw_text)
        role_scores = self._role_scores(raw_text)
        recommended_roles = self._recommend_roles(role_scores)

        selected_role = target_role if target_role in ROLE_SKILLS else (recommended_roles[0] if recommended_roles else self.role_names[0])
        evaluation_5d = self.evaluate_5d(raw_text, skills, selected_role, role_scores)

        ats_score = evaluation_5d["overall_5d_score"]
        missing_skills = [skill for skill in ROLE_SKILLS[selected_role] if skill not in skills]
        suggestions = self._suggestions(skills, recommended_roles, ats_score, evaluation_5d)
        role_score = float(role_scores[self.role_names.index(selected_role)]) if selected_role in self.role_names else 0.0

        return {
            "success": True,
            "skills_detected": skills,
            "recommended_roles": recommended_roles,
            "ats_score": ats_score,
            "evaluation_5d": evaluation_5d,
            "improvement_suggestions": suggestions,
            "skill_score": int(round(min(len(skills), len(ROLE_SKILLS[selected_role])) / len(ROLE_SKILLS[selected_role]) * 40)),
            "project_score": evaluation_5d["dimensions"]["impact"]["score"],
            "keyword_score": int(round(role_score * 15)),
            "education_score": evaluation_5d["dimensions"]["structure"]["score"],
            "extracted_skills": skills,
            "domain": selected_role,
            "recommended_careers": recommended_roles,
            "recommended_job_roles": recommended_roles,
            "recommended_certifications": [f"Certified {selected_role} Associate", f"Advanced {skills[0].capitalize() if skills else 'Python'} Specialist"],
            "recommended_higher_studies": ["M.Tech Computer Engineering", "M.S. in Software Systems", "Post Graduate Diploma in AI/ML"],
            "skill_gaps": missing_skills,
            "learning_roadmap": [{"step": index + 1, "task": f"Master {skill} with hands-on projects"} for index, skill in enumerate(missing_skills[:5])],
            "industry_recommendations": ["FinTech & Payments", "Cloud & SaaS Infrastructure", "AI & Autonomous Systems"],
        }
