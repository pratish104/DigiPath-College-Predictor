"""Deterministic, offline-safe resume parsing and ATS scoring."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

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
    "Backend Engineer": ("python", "fastapi", "django", "flask", "sql", "postgresql", "mysql", "api", "docker", "git"),
    "Full Stack Developer": ("python", "javascript", "typescript", "react", "node js", "html", "css", "sql", "docker"),
    "Data Analyst": ("python", "sql", "excel", "power bi", "tableau", "pandas", "numpy", "statistics"),
    "ML Engineer": ("python", "machine learning", "scikit learn", "tensorflow", "pytorch", "pandas", "numpy", "mlops"),
    "DevOps Engineer": ("docker", "kubernetes", "aws", "linux", "ci cd", "jenkins", "terraform", "git"),
    "Cybersecurity Analyst": ("network security", "owasp", "siem", "wireshark", "incident response", "python", "linux"),
}

SKILL_VOCABULARY = tuple(sorted({skill for skills in ROLE_SKILLS.values() for skill in skills}, key=str.casefold))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _keyword_pattern(skill: str) -> re.Pattern[str]:
    token_pattern = r"\s+".join(re.escape(token) for token in skill.split())
    return re.compile(rf"(?<![a-z0-9]){token_pattern}(?![a-z0-9])", flags=re.IGNORECASE)


class ResumeService:
    """Analyzes one local PDF or DOCX without network access or mutable NLP state."""

    def __init__(self) -> None:
        self.role_names = tuple(ROLE_SKILLS.keys())
        self.role_documents = tuple(" ".join(ROLE_SKILLS[role]) for role in self.role_names)
        self.vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2), sublinear_tf=True)
        self.role_matrix = self.vectorizer.fit_transform(self.role_documents)
        self.skill_patterns = {skill: _keyword_pattern(skill) for skill in SKILL_VOCABULARY}
        self.count_vectorizer = CountVectorizer(vocabulary={skill: index for index, skill in enumerate(SKILL_VOCABULARY)}, binary=True, lowercase=True)

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
        resume_vector = self.vectorizer.transform([text])
        return cosine_similarity(resume_vector, self.role_matrix).ravel()

    def _recommend_roles(self, scores: np.ndarray) -> List[str]:
        ordering = np.argsort(-scores, kind="stable")
        return [self.role_names[index] for index in ordering[:3] if scores[index] > 0.0]

    def _ats_score(self, skills: Iterable[str], target_role: Optional[str], role_scores: np.ndarray) -> int:
        canonical_target = target_role if target_role in ROLE_SKILLS else None
        if canonical_target is not None:
            role_index = self.role_names.index(canonical_target)
        else:
            role_index = int(np.argmax(role_scores))
        target_skills = ROLE_SKILLS[self.role_names[role_index]]
        candidate_document = " ".join(skills)
        count_matrix = self.count_vectorizer.transform([candidate_document, " ".join(target_skills)])
        intersection = count_matrix.minimum(count_matrix[1]).sum()
        target_count = count_matrix[1].sum()
        coverage = float(intersection / target_count) if target_count else 0.0
        semantic = float(np.clip(role_scores[role_index], 0.0, 1.0))
        return int(round(np.clip((coverage * 0.7 + semantic * 0.3) * 100.0, 0.0, 100.0)))

    def _suggestions(self, skills: List[str], recommended_roles: List[str], ats_score: int) -> List[str]:
        primary_role = recommended_roles[0] if recommended_roles else self.role_names[0]
        missing = [skill for skill in ROLE_SKILLS[primary_role] if skill not in skills]
        suggestions = [f"Add evidence of {skill} for {primary_role} roles." for skill in missing[:3]]
        if ats_score < 50:
            suggestions.append("Add measurable outcomes to project and experience bullet points.")
        if not skills:
            suggestions.append("Add a clearly labeled technical skills section using role-specific terms.")
        return suggestions

    def analyze(self, file_path: str | Path, interests: Optional[List[str]] = None, target_role: Optional[str] = None) -> Dict[str, Any]:
        raw_text = self.extract_text(file_path)
        skills = self.extract_skills(raw_text)
        role_scores = self._role_scores(raw_text)
        recommended_roles = self._recommend_roles(role_scores)
        ats_score = self._ats_score(skills, target_role, role_scores)
        selected_role = target_role if target_role in ROLE_SKILLS else (recommended_roles[0] if recommended_roles else self.role_names[0])
        missing_skills = [skill for skill in ROLE_SKILLS[selected_role] if skill not in skills]
        suggestions = self._suggestions(skills, recommended_roles, ats_score)
        role_score = float(role_scores[self.role_names.index(selected_role)]) if selected_role in self.role_names else 0.0
        return {
            "success": True,
            "skills_detected": skills,
            "recommended_roles": recommended_roles,
            "ats_score": ats_score,
            "improvement_suggestions": suggestions,
            "skill_score": int(round(min(len(skills), len(ROLE_SKILLS[selected_role])) / len(ROLE_SKILLS[selected_role]) * 40)),
            "project_score": 0,
            "keyword_score": int(round(role_score * 15)),
            "education_score": 0,
            "extracted_skills": skills,
            "domain": selected_role,
            "recommended_careers": recommended_roles,
            "recommended_job_roles": recommended_roles,
            "recommended_certifications": [],
            "recommended_higher_studies": [],
            "skill_gaps": missing_skills,
            "learning_roadmap": [{"step": index + 1, "task": f"Develop {skill}"} for index, skill in enumerate(missing_skills[:5])],
            "industry_recommendations": [],
        }
