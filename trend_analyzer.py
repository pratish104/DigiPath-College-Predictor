import pandas as pd
from data_loader import DataLoader
import numpy as np

class TrendAnalyzer:
    def __init__(self, data_loader: DataLoader):
        self.data_loader = data_loader
        self.cet_data = self.data_loader.get_combined_cet_data()

    def get_forecasts(self):
        if self.cet_data.empty:
            return {}

        # Filter for 2024 and 2025 data
        df = self.cet_data.copy()
        
        # We need data that has year info
        df_2024 = df[df['year'] == 2024]
        df_2025 = df[df['year'] == 2025]

        if df_2024.empty or df_2025.empty:
            # If explicit year is missing, try to infer from filename-based loading 
            # (which my data_loader doesn't yet preserve, so let's use a simpler heuristic if needed)
            return {"status": "Incomplete Data for Forecasting"}

        # Pivot to get branch-category pairs across years
        pivot = df.pivot_table(index=['college_code', 'branch', 'category'], columns='year', values='cutoff_value')
        
        if 2024 not in pivot.columns or 2025 not in pivot.columns:
            return {"status": "Incomplete Data for Forecasting"}

        # Calculate growth
        pivot['diff'] = pivot[2025] - pivot[2024]
        pivot['forecast_2027'] = pivot[2025] + (pivot['diff'] * 1.5)
        
        # Bound forecasts to 100%
        pivot['forecast_2027'] = pivot['forecast_2027'].apply(lambda x: min(100, max(0, x)) if not np.isnan(x) else np.nan)

        return pivot.reset_index().to_dict(orient='records')[:100] # Sample top 100

    def get_branch_trends(self):
        if self.cet_data.empty:
            return []
        
        # Average cutoff per branch can indicate demand
        trends = self.cet_data.groupby('branch').agg({
            'cutoff_value': 'mean',
            'college_name': 'count'
        }).rename(columns={'cutoff_value': 'avg_cutoff', 'college_name': 'count'})
        
        trends = trends.sort_values(by='avg_cutoff', ascending=False)
        return trends.reset_index().to_dict(orient='records')

    def get_city_trends(self):
        if self.cet_data.empty:
            return []
            
        trends = self.cet_data.groupby('city').agg({
            'cutoff_value': 'mean',
            'college_name': 'count'
        }).rename(columns={'cutoff_value': 'avg_cutoff', 'college_name': 'count'})
        
        trends = trends.sort_values(by='avg_cutoff', ascending=False)
        return trends.reset_index().to_dict(orient='records')

    def get_competition_analysis(self):
        # Competition is high where standard deviation is low and average is high
        if self.cet_data.empty:
            return []
            
        analysis = self.cet_data.groupby(['branch', 'city']).agg({
            'cutoff_value': ['mean', 'std', 'count']
        })
        analysis.columns = ['avg_cutoff', 'std_cutoff', 'count']
        
        # Heuristic: Competition Score = avg_cutoff / (std_cutoff + 1)
        analysis['competition_score'] = analysis['avg_cutoff'] / (analysis['std_cutoff'] + 0.1)
        analysis = analysis.sort_values(by='competition_score', ascending=False)
        
        return analysis.reset_index().to_dict(orient='records')[:20]
