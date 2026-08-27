import pandas as pd
from data_loader import DataLoader
import os
import math
import numpy as np
import logging

log = logging.getLogger("digipath.cet_predictor")

def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    if isinstance(obj, np.generic):
        return obj.item()
    return obj

class CETPredictor:
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.data = self.data_loader.get_combined_cet_data()
        log.info(f"🔧 CETPredictor initialized with {len(self.data)} rows")

    def calculate_probability(self, user_val, cutoff_val):
        diff = user_val - cutoff_val
        if diff >= 3:
            return "Very High", 95
        elif diff >= 1:
            return "High", 80
        elif diff >= -1:
            return "Moderate", 60
        elif diff >= -3:
            return "Low", 30
        else:
            return "Very Low", 10

    def predict(self, percentile, category, branch=None, city=None, college_type=None, extra_filters=None):
        """
        Main prediction engine with full exception handling and logging.
        """
        try:
            log.info(f"📊 CET Prediction requested: percentile={percentile}, category={category}, branch={branch}, city={city}")
            
            # Validate input
            if not percentile or percentile < 0 or percentile > 100:
                log.warning(f"⚠️ Invalid percentile: {percentile}")
                return []
            
            if self.data.empty:
                log.error("❌ No CET data available")
                return []

            df = self.data.copy()
            log.info(f"📋 Starting with {len(df)} total records")

            # Core Filtering - More robust category matching
            if category and category.lower() != "all":
                try:
                    # Some datasets use GOPENS, others use OPEN. We try to match the most relevant.
                    df = df[df['category'].str.contains(category, na=False, case=False) | 
                            (df['category'].str.contains("OPEN", na=False, case=False) if category.upper() == "OPEN" else False)]
                    log.info(f"✓ After category filter: {len(df)} records")
                except Exception as e:
                    log.error(f"❌ Category filtering failed: {e}")
                    return []
            
            if branch and branch.lower() != "all":
                try:
                    # Handle cases like "Computer Engineering" vs "Computer Science"
                    branch_terms = branch.lower().split()
                    # If branch has multiple words, try to match all of them or the main one
                    pattern = "|".join(branch_terms)
                    df = df[df['branch'].str.contains(pattern, na=False, case=False)]
                    log.info(f"✓ After branch filter: {len(df)} records")
                except Exception as e:
                    log.error(f"❌ Branch filtering failed: {e}")
                    return []
                
            if city and city.lower() != "all":
                try:
                    df = df[df['city'].str.contains(city, na=False, case=False)]
                    log.info(f"✓ After city filter: {len(df)} records")
                except Exception as e:
                    log.error(f"❌ City filtering failed: {e}")
                    return []

            # Advanced Filtering
            if extra_filters:
                try:
                    for col, val in extra_filters.items():
                        if col in df.columns and val and str(val).lower() != "all":
                            df = df[df[col].astype(str).str.contains(str(val), na=False, case=False)]
                    log.info(f"✓ After extra filters: {len(df)} records")
                except Exception as e:
                    log.error(f"❌ Extra filtering failed: {e}")
                    return []

            if df.empty:
                log.warning("⚠️ No records match the filters")
                return []

            results = []
            
            try:
                # Grouping to avoid duplicates across years/stages
                # We prefer the most recent data if year is available
                if 'year' in df.columns:
                    df = df.sort_values(by='year', ascending=False)
                    
                group_cols = ['college_code', 'branch', 'category']
                
                # Aggregation logic: take the most recent cutoff
                grouped = df.groupby(group_cols).agg({
                    'college_name': 'first',
                    'city': 'first',
                    'cutoff_value': 'first', # Since we sorted by year
                    'year': 'first'
                }).reset_index()

                log.info(f"📦 Grouped into {len(grouped)} unique combinations")

                for idx, row in grouped.iterrows():
                    try:
                        cutoff = row['cutoff_value']
                        
                        # Ranking & Classification Factors
                        diff = percentile - cutoff
                        
                        if diff >= 3:
                            classification = "Safe"
                            prob_label, prob_percent = "Very High", 95
                        elif diff >= 0:
                            classification = "Moderate"
                            prob_label, prob_percent = "High", 80
                        elif diff >= -3:
                            classification = "Dream"
                            prob_label, prob_percent = "Low", 40
                        else:
                            classification = "Dream"
                            prob_label, prob_percent = "Very Low", 15

                        # Enhanced Predicted Cutoff 2026 
                        # (Heuristic: +0.45 if high competition, +0.2 if low)
                        increase = 0.45 if cutoff > 90 else 0.25
                        predicted_cutoff = round(float(cutoff) + increase, 2)

                        c_type = self.data_loader.get_college_type(row['college_code'])
                        
                        # Filter by college type if specified
                        if college_type and college_type.lower() != "all":
                            if college_type.lower() not in c_type.lower():
                                continue

                        # Add placement score if available from institutes.json
                        placement_score = 0
                        inst_info = self.data_loader.institutes_data.get(str(row['college_code']))
                        if inst_info:
                            stats = inst_info.get("placement_stats", {})
                            placement_score = stats.get("placement_rate", 0)

                        res_item = {
                            "rank": 0, # Will be set after sorting
                            "college_name": row['college_name'],
                            "college_code": row['college_code'],
                            "branch": row['branch'],
                            "city": row['city'],
                            "college_type": c_type,
                            "category_used": row['category'],
                            "cutoff": round(float(cutoff), 2),
                            "predicted_cutoff": predicted_cutoff,
                            "probability_label": prob_label,
                            "probability_percent": prob_percent,
                            "classification": classification,
                            "score_diff": round(diff, 2),
                            "placement_score": placement_score,
                            "year": row.get('year', 'N/A')
                        }
                        results.append(res_item)
                    except Exception as e:
                        log.error(f"❌ Error processing row {idx}: {e}")
                        continue

            except Exception as e:
                log.error(f"❌ Grouping/aggregation failed: {e}")
                return []

            if not results:
                log.warning("⚠️ No results after processing")
                return []

            # Final Sorting Strategy: 
            # 1. Classification (Safe > Moderate > Dream)
            # 2. Score Diff (Descending)
            # 3. Placement Score (Descending)
            order = {"Safe": 0, "Moderate": 1, "Dream": 2}
            results = sorted(results, key=lambda x: (order[x['classification']], -x['score_diff'], -x['placement_score']))
            
            # Assign Ranks
            for i, item in enumerate(results):
                item["rank"] = i + 1

            log.info(f"✅ CET Prediction complete: {len(results)} results returned")
            return sanitize(results)

        except Exception as e:
            log.exception(f"❌ CRITICAL: CET Prediction failed: {e}")
            return []
