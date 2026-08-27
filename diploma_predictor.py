import pandas as pd
from data_loader import DataLoader
import os
import math
import numpy as np
import logging

log = logging.getLogger("digipath.diploma_predictor")

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

class DiplomaPredictor:
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.data = self.data_loader.get_combined_diploma_data()
        log.info(f"🔧 DiplomaPredictor initialized with {len(self.data)} rows")

    def calculate_probability(self, user_val, cutoff_val):
        # Diploma cutoffs are usually more stable but high
        diff = user_val - cutoff_val
        if diff >= 2:
            return "Very High", 95
        elif diff >= 0.5:
            return "High", 85
        elif diff >= -0.5:
            return "Moderate", 65
        elif diff >= -2:
            return "Low", 35
        else:
            return "Very Low", 15

    def predict(self, percentage, category, branch=None, city=None, college_type=None, extra_filters=None):
        """
        Main prediction engine with full exception handling and logging.
        """
        try:
            log.info(f"📊 Diploma Prediction requested: percentage={percentage}, category={category}, branch={branch}, city={city}")
            
            # Validate input
            if not percentage or percentage < 0 or percentage > 100:
                log.warning(f"⚠️ Invalid percentage: {percentage}")
                return []
            
            if self.data.empty:
                log.error("❌ No Diploma data available")
                return []

            df = self.data.copy()
            log.info(f"📋 Starting with {len(df)} total records")

            # Core Filtering
            if category and category.lower() != "all":
                try:
                    df = df[df['category'].str.contains(category, na=False, case=False) | 
                            (df['category'].str.contains("OPEN", na=False, case=False) if category.upper() == "OPEN" else False)]
                    log.info(f"✓ After category filter: {len(df)} records")
                except Exception as e:
                    log.error(f"❌ Category filtering failed: {e}")
                    return []
            
            if branch and branch.lower() != "all":
                try:
                    branch_terms = branch.lower().split()
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
                # Prefer most recent data
                if 'year' in df.columns:
                    df = df.sort_values(by='year', ascending=False)

                group_cols = ['college_code', 'branch', 'category']
                
                grouped = df.groupby(group_cols).agg({
                    'college_name': 'first',
                    'city': 'first',
                    'cutoff_value': 'first',
                    'year': 'first'
                }).reset_index()

                log.info(f"📦 Grouped into {len(grouped)} unique combinations")

                for idx, row in grouped.iterrows():
                    try:
                        cutoff = row['cutoff_value']
                        
                        # Ranking & Classification Factors (Diploma percentages are often tighter)
                        diff = percentage - cutoff
                        
                        if diff >= 5:
                            classification = "Safe"
                            prob_label, prob_percent = "Very High", 98
                        elif diff >= 0:
                            classification = "Moderate"
                            prob_label, prob_percent = "High", 85
                        elif diff >= -3:
                            classification = "Dream"
                            prob_label, prob_percent = "Low", 35
                        else:
                            classification = "Dream"
                            prob_label, prob_percent = "Very Low", 10

                        # Predicted Cutoff 2026 (Diploma cutoffs are rising fast)
                        predicted_cutoff = round(float(cutoff) + 0.65, 2)

                        c_type = self.data_loader.get_college_type(row['college_code'])
                        
                        if college_type and college_type.lower() != "all":
                            if college_type.lower() not in c_type.lower():
                                continue

                        # Add placement score
                        placement_score = 0
                        inst_info = self.data_loader.institutes_data.get(str(row['college_code']))
                        if inst_info:
                            stats = inst_info.get("placement_stats", {})
                            placement_score = stats.get("placement_rate", 0)

                        res_item = {
                            "rank": 0,
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

            order = {"Safe": 0, "Moderate": 1, "Dream": 2}
            results = sorted(results, key=lambda x: (order[x['classification']], -x['score_diff'], -x['placement_score']))
            
            for i, item in enumerate(results):
                item["rank"] = i + 1

            log.info(f"✅ Diploma Prediction complete: {len(results)} results returned")
            return sanitize(results)

        except Exception as e:
            log.exception(f"❌ CRITICAL: Diploma Prediction failed: {e}")
            return []
