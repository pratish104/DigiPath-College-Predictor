import os
import json
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from typing import List, Dict, Any

class ChatService:
    def __init__(self, data_loader):
        self.data_loader = data_loader
        self.vectorizer = TfidfVectorizer(stop_words='english', ngram_range=(1, 2))
        self.corpus = []
        self.source_map = []
        self.build_corpus()

    def build_corpus(self):
        # 1. Add Institutes knowledge (Full)
        for code, info in self.data_loader.institutes_data.items():
            name = info.get('name', 'Unknown')
            loc = info.get('location', 'Maharashtra')
            ov = info.get("system_overview", {})
            pm = info.get("placement_matrix", {})
            
            text = (f"Institute {name} ({code}) located in {loc}. "
                    f"Status: {ov.get('status')}. Autonomy: {ov.get('autonomy')}. University: {ov.get('university')}. "
                    f"Placements: Average {pm.get('average_package')}, Highest {pm.get('highest_package')}. "
                    f"Top Recruiters: {', '.join(pm.get('top_recruiters', []))}. "
                    f"Website: {info.get('website', 'N/A')}. Contact: {info.get('phone', 'N/A')}.")
            
            self.corpus.append(text)
            self.source_map.append({"type": "institute", "id": code, "name": name})

        # 2. Add Cutoff Trends (More rows)
        cutoff_data = self.data_loader.get_combined_cet_data()
        if not cutoff_data.empty:
            # Group by college and branch to give summarized cutoff info
            summarized = cutoff_data.groupby(['college_name', 'branch', 'category']).agg({'cutoff_value': ['min', 'max', 'mean']}).reset_index()
            for _, row in summarized.head(1000).iterrows():
                text = (f"Cutoff for {row['college_name']} branch {row['branch']} "
                        f"category {row['category']} ranges from {row[('cutoff_value', 'min')]} to {row[('cutoff_value', 'max')]} "
                        f"with an average of {round(row[('cutoff_value', 'mean')], 2)}.")
                self.corpus.append(text)
                self.source_map.append({"type": "cutoff", "data": row.to_dict()})

        # 3. Platform Knowledge
        platform_info = [
            "DigiPath is an AI-powered career guidance platform for engineering and diploma admissions in Maharashtra.",
            "DigiPath provides college prediction based on MHT-CET and Diploma scores.",
            "DigiPath offers AI Resume Analysis with ATS scoring and career roadmaps.",
            "You can report admission scams or fake recruiters using the Scam Detector module.",
            "The platform uses historical CAP round data to predict the most likely college for your percentile."
        ]
        for info in platform_info:
            self.corpus.append(info)
            self.source_map.append({"type": "platform"})

        if self.corpus:
            self.tfidf_matrix = self.vectorizer.fit_transform(self.corpus)

    def query(self, user_query: str) -> str:
        if not self.corpus:
            return "I am still learning from the datasets. Please ask me about colleges or placements."

        query_vec = self.vectorizer.transform([user_query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        best_idx = np.argmax(similarities)
        
        if similarities[best_idx] < 0.15: # Higher threshold for better accuracy
            return ("I couldn't find specific details for that. Try asking about:\n"
                    "- A specific college (e.g., 'VJTI placements')\n"
                    "- Cutoffs (e.g., 'COEP CO cutoff')\n"
                    "- Admissions (e.g., 'How does DigiPath predict colleges?')")

        best_match = self.corpus[best_idx]
        source = self.source_map[best_idx]
        
        if source["type"] == "institute":
            return f"🏢 **{source['name']} Info:**\n{best_match}"
        elif source["type"] == "cutoff":
            return f"📊 **Cutoff Analysis:**\n{best_match}"
        elif source["type"] == "platform":
            return f"🤖 **DigiPath Assistant:**\n{best_match}"
        
        return best_match

    def get_contextual_response(self, user_query: str, history: List[Dict[str, str]] = None) -> str:
        user_query_lower = user_query.lower()
        
        # 1. Intent Detection
        if any(w in user_query_lower for w in ["predict", "my score", "percentile", "get into"]):
            return "To get a personalized college prediction, please use the **PREDICTOR** module. Once you have the results, I can help you analyze specific colleges from your list!"
        
        if any(w in user_query_lower for w in ["resume", "cv", "ats", "analysis"]):
            return "You can upload your resume in the **RESUME_AI** section. I'll analyze your ATS score, extract skills, and suggest a career roadmap for you."

        if "hello" in user_query_lower or "hi" in user_query_lower:
            return "Hello! I am the DigiPath AI Assistant. How can I help you today? You can ask me about college cutoffs, placements, or platform features."

        # 2. Contextual Dataset Search
        return self.query(user_query)
