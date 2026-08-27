import os
import re
import json
import pdfplumber
import spacy
import nltk
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import pandas as pd
import numpy as np
from typing import Dict, Any, List

# Initialize spaCy and NLTK
try:
    nlp = spacy.load("en_core_web_sm")
except:
    os.system("python -m spacy download en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")

nltk.download('stopwords', quiet=True)
nltk.download('punkt', quiet=True)
STOPWORDS = set(stopwords.words('english'))

class ResumeService:
    def __init__(self, data_loader):
        self.data_loader = data_loader
        self.skills_db = {
            "Software Development": ["Python", "Java", "C++", "JavaScript", "React", "Node.js", "Docker", "Kubernetes", "SQL", "NoSQL", "Git", "CI/CD", "TypeScript", "HTML", "CSS", "Express", "MongoDB", "PostgreSQL"],
            "Data Science & AI": ["Machine Learning", "Deep Learning", "TensorFlow", "PyTorch", "Pandas", "NumPy", "Scikit-learn", "NLP", "Computer Vision", "R", "Spark", "Keras", "Matplotlib", "Seaborn", "XGBoost", "LLM", "GenAI"],
            "Mechanical Engineering": ["AutoCAD", "SolidWorks", "MATLAB", "Thermodynamics", "Fluid Mechanics", "CAD", "CAM", "ANSYS", "Finite Element Analysis", "Mechatronics", "Manufacturing", "Robotics", "Control Systems"],
            "Civil Engineering": ["Staad Pro", "Revit", "Surveying", "Structural Analysis", "Geotechnical Engineering", "Construction Management", "Hydrology", "AutoCAD Civil 3D", "ETABS", "BIM"],
            "Finance & Business": ["Excel", "Financial Modeling", "Accounting", "Marketing Strategy", "Project Management", "Business Analysis", "Tableau", "Power BI", "Corporate Finance", "Risk Management", "Investment Banking"],
            "Cyber Security": ["Penetration Testing", "Network Security", "Ethical Hacking", "Firewalls", "Cryptography", "SIEM", "Incident Response", "Wireshark", "Metasploit", "Kali Linux", "OWASP"],
            "Space Technology": ["Orbital Mechanics", "Satellite Communications", "Propulsion Systems", "Remote Sensing", "Astronomy", "Astrophysics", "GNSS", "Aerospace Engineering", "Spacecraft Design"],
            "Marketing & Sales": ["SEO", "SEM", "Content Marketing", "Social Media Marketing", "Google Analytics", "CRM", "Salesforce", "Email Marketing", "Brand Management"],
            "Research & Academia": ["Scientific Writing", "Statistical Analysis", "Research Methodology", "Grant Writing", "Data Collection", "Literature Review"]
        }
        self.vectorizer = TfidfVectorizer(stop_words='english')
        self._initialize_vectorizer()

    def _initialize_vectorizer(self):
        # Create a corpus from skill labels for better domain matching
        domain_corpus = [" ".join(skills) for skills in self.skills_db.values()]
        self.tfidf_matrix = self.vectorizer.fit_transform(domain_corpus)
        self.domain_list = list(self.skills_db.keys())

    def extract_text(self, file_path: str) -> str:
        text = ""
        try:
            if file_path.endswith('.pdf'):
                with pdfplumber.open(file_path) as pdf:
                    for page in pdf.pages:
                        text += page.extract_text() or ""
            elif file_path.endswith('.docx'):
                import docx
                doc = docx.Document(file_path)
                for para in doc.paragraphs:
                    text += para.text + "\n"
        except Exception as e:
            print(f"Error extracting text: {e}")
        return text

    def clean_text(self, text: str) -> str:
        text = text.lower()
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        words = nltk.word_tokenize(text)
        words = [w for w in words if w not in STOPWORDS]
        return " ".join(words)

    def extract_skills(self, text: str) -> List[str]:
        found_skills = []
        text_lower = text.lower()
        # Direct keyword matching
        for domain, skills in self.skills_db.items():
            for skill in skills:
                # Use word boundaries to avoid partial matches (e.g., 'R' in 'React')
                pattern = r'\b' + re.escape(skill.lower()) + r'\b'
                if re.search(pattern, text_lower):
                    found_skills.append(skill)
        
        # Also use spaCy for Entity Recognition if needed, but keyword is more precise for skills
        return list(set(found_skills))

    def classify_domain(self, text: str, interests: List[str] = None) -> str:
        cleaned_text = self.clean_text(text)
        query_vec = self.vectorizer.transform([cleaned_text])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        
        best_idx = np.argmax(similarities)
        domain = self.domain_list[best_idx]
        
        # Boost based on user interests if similarity is close
        if interests:
            for interest in interests:
                for i, d in enumerate(self.domain_list):
                    if interest.lower() in d.lower():
                        similarities[i] += 0.2 # Interest boost
            domain = self.domain_list[np.argmax(similarities)]
            
        return domain

    def calculate_ats_score(self, text: str, skills: List[str]) -> Dict[str, Any]:
        text_lower = text.lower()
        
        # 1. Skill Match (40 pts)
        skill_score = min(len(skills) * 4, 40)
        
        # 2. Education (15 pts)
        education_keywords = ["bachelor", "master", "phd", "degree", "university", "college", "iit", "nit", "institute", "b.e", "b.tech", "m.tech", "diploma"]
        edu_count = sum(1 for k in education_keywords if k in text_lower)
        edu_score = min(edu_count * 3, 15)
        
        # 3. Projects (20 pts)
        project_keywords = ["project", "implemented", "developed", "built", "designed", "research", "repository", "github", "deployed"]
        proj_count = sum(1 for k in project_keywords if k in text_lower)
        proj_score = min(proj_count * 4, 20)
        
        # 4. Keywords (15 pts) - Density of domain keywords
        domain_keywords = [s.lower() for d in self.skills_db.values() for s in d]
        found_keywords = sum(1 for k in domain_keywords if k in text_lower)
        keyword_score = min(found_keywords / 5, 15)
        
        # 5. Experience/Professionalism (10 pts)
        experience_keywords = ["experience", "years", "intern", "professional", "working", "achieved", "led", "managed"]
        exp_score = min(sum(2 for k in experience_keywords if k in text_lower), 10)
        
        total = skill_score + edu_score + proj_score + keyword_score + exp_score
        
        return {
            "overall": round(total, 2),
            "skill_match": round(skill_score, 2),
            "education": round(edu_score, 2),
            "projects": round(proj_score, 2),
            "keywords": round(keyword_score, 2),
            "experience": round(exp_score, 2)
        }

    def generate_recommendations(self, domain: str, skills: List[str], interests: List[str] = None) -> Dict[str, Any]:
        # Merge Domain and Interests for better recommendation context
        combined_context = [domain] + (interests if interests else [])
        
        # Knowledge Base for Recommendations (Expanded)
        kb = {
            "Software Development": {
                "roles": ["Full Stack Developer", "Backend Engineer", "Mobile App Developer", "DevOps Engineer"],
                "certs": ["AWS Certified Developer", "Google Professional Cloud Developer", "Oracle Java Certification"],
                "studies": ["MS in Computer Science", "M.Tech in Software Engineering"],
                "industries": ["IT Services", "FinTech", "E-commerce", "SaaS"]
            },
            "Data Science & AI": {
                "roles": ["Data Scientist", "ML Engineer", "Data Analyst", "AI Researcher", "BI Developer"],
                "certs": ["DeepLearning.AI TensorFlow Developer", "Azure Data Scientist Associate", "Google Data Analytics"],
                "studies": ["MS in Data Science", "Ph.D. in Artificial Intelligence", "PG Diploma in ML"],
                "industries": ["Healthcare", "Finance", "Tech Giants", "Retail Analytics"]
            },
            "Mechanical Engineering": {
                "roles": ["Design Engineer", "Manufacturing Engineer", "Automotive Engineer", "Robotics Engineer", "HVAC Engineer"],
                "certs": ["Certified SolidWorks Professional (CSWP)", "Six Sigma Green Belt", "Autodesk Certified Professional"],
                "studies": ["M.Tech in Machine Design", "MS in Robotics", "MBA in Operations"],
                "industries": ["Automotive", "Aerospace", "Manufacturing", "Energy"]
            },
            "Space Technology": {
                "roles": ["Satellite Data Analyst", "Space Systems Engineer", "Mission Operations Lead", "Propulsion Engineer", "Remote Sensing Specialist"],
                "certs": ["STK Certified", "Remote Sensing Professional", "GIS Certification"],
                "studies": ["MS in Astronautics", "Masters in Space Studies", "Ph.D. in Astrophysics"],
                "industries": ["Space Agencies (ISRO/NASA)", "Private Space (SpaceX/Blue Origin)", "Defense"]
            },
            "Finance & Business": {
                "roles": ["Financial Analyst", "Investment Banker", "Risk Manager", "Business Consultant", "Portfolio Manager"],
                "certs": ["CFA Level 1", "FRM Certification", "CPA"],
                "studies": ["MBA in Finance", "Masters in Financial Engineering"],
                "industries": ["Banking", "Insurance", "Venture Capital", "Corporate Finance"]
            },
            "Cyber Security": {
                "roles": ["Security Analyst", "Ethical Hacker", "Security Architect", "Incident Responder", "Compliance Officer"],
                "certs": ["CEH (Certified Ethical Hacker)", "CISSP", "CompTIA Security+"],
                "studies": ["MS in Cyber Security", "M.Tech in Information Security"],
                "industries": ["Cybersecurity Firms", "Banking", "Government", "Consulting"]
            }
        }

        # Select the best KB entry based on interest-boosted domain
        active_kb = kb.get(domain, kb["Software Development"])
        
        # Adjust roles if special interests are present (e.g. Space + ML)
        if interests:
            if "Space" in str(interests) and "AI" in domain:
                active_kb["roles"] = ["Space Data Analyst", "Satellite Image Processor", "AI-driven Mission Planner"]
            elif "Finance" in str(interests) and "Software" in domain:
                active_kb["roles"] = ["FinTech Developer", "Algorithmic Trading Engineer", "Blockchain Developer"]

        recommendations = {
            "careers": [f"Senior {domain} Specialist", f"{domain} Architect", f"Strategic {domain} Consultant"],
            "job_roles": active_kb["roles"],
            "certifications": active_kb["certs"],
            "higher_studies": active_kb["studies"],
            "industries": active_kb["industries"],
            "skill_gaps": [],
            "roadmap": []
        }
        
        # Skill Gaps - logic to find what's missing in the domain
        domain_skills = self.skills_db.get(domain, [])
        recommendations["skill_gaps"] = [s for s in domain_skills if s not in skills][:5]
        
        # Dynamic Roadmap
        recommendations["roadmap"] = [
            {"step": 1, "task": f"Strengthen Core: {', '.join(skills[:3])}"},
            {"step": 2, "task": f"Learn missing essentials: {', '.join(recommendations['skill_gaps'][:3])}"},
            {"step": 3, "task": f"Prepare for {recommendations['certifications'][0]} certification"},
            {"step": 4, "task": f"Build a portfolio project in {recommendations['industries'][0]} sector"},
            {"step": 5, "task": f"Target {recommendations['job_roles'][0]} roles in 3-6 months"}
        ]

        return recommendations

    def analyze(self, file_path: str, interests: List[str] = None) -> Dict[str, Any]:
        raw_text = self.extract_text(file_path)
        if not raw_text.strip():
            return {"error": "Could not extract text from file."}

        skills = self.extract_skills(raw_text)
        domain = self.classify_domain(raw_text, interests)
        ats = self.calculate_ats_score(raw_text, skills)
        recs = self.generate_recommendations(domain, skills, interests)
        
        # Heuristic suggestions
        suggestions = []
        if ats["overall"] < 50: suggestions.append("Consider a significant overhaul of your resume structure and content.")
        if ats["skill_match"] < 25: suggestions.append("Your skill list is sparse. Add more domain-specific technical keywords.")
        if ats["projects"] < 10: suggestions.append("Your projects section is weak. Use 'Action Verbs' and quantify results.")
        if ats["keywords"] < 10: suggestions.append("Missing critical industry keywords. Align your vocabulary with job descriptions.")
        
        return {
            "ats_score": ats["overall"],
            "skill_score": ats["skill_match"],
            "project_score": ats["projects"],
            "keyword_score": ats["keywords"],
            "education_score": ats["education"],
            "extracted_skills": skills,
            "domain": domain,
            "improvement_suggestions": suggestions,
            "recommended_careers": recs["careers"],
            "recommended_job_roles": recs["job_roles"],
            "recommended_certifications": recs["certifications"],
            "recommended_higher_studies": recs["higher_studies"],
            "skill_gaps": recs["skill_gaps"],
            "learning_roadmap": recs["roadmap"],
            "industry_recommendations": recs["industries"]
        }
