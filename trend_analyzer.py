"""Historical CET analytics over validated 2024 and 2025 fact-table cohorts."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from data_loader import (
    COL_BRANCH,
    COL_CATEGORY,
    COL_CITY,
    COL_COLLEGE_CODE,
    COL_COLLEGE_NAME,
    COL_CUTOFF,
    COL_EXAM_TYPE,
    COL_QUOTA,
    COL_SEAT_TYPE,
    COL_YEAR,
    DataLoader,
)


class TrendAnalyzer:
    """Computes trends only for CET cohorts present in both official years."""

    _YEARS = (2024, 2025)
    _GRAIN = [COL_COLLEGE_CODE, COL_BRANCH, COL_CATEGORY, COL_QUOTA, COL_SEAT_TYPE]

    def __init__(self, data_loader: DataLoader) -> None:
        self.data_loader = data_loader

    def _multi_year_cohorts(self) -> pd.DataFrame:
        cet = self.data_loader.get_all_data()
        cet = cet.loc[cet[COL_EXAM_TYPE].eq("CET") & cet[COL_YEAR].isin(self._YEARS)].copy()
        if cet.empty:
            return cet
        year_count = cet.groupby(self._GRAIN, dropna=False)[COL_YEAR].nunique()
        complete = year_count.loc[year_count.eq(len(self._YEARS))].index
        if complete.empty:
            return cet.iloc[0:0].copy()
        complete_frame = complete.to_frame(index=False)
        return cet.merge(complete_frame, on=self._GRAIN, how="inner", validate="many_to_one")

    def get_forecasts(self) -> dict[str, Any]:
        cohorts = self._multi_year_cohorts()
        if cohorts.empty:
            return {"status": "Insufficient Historical Data", "records": []}
        pivot = cohorts.pivot_table(index=self._GRAIN, columns=COL_YEAR, values=COL_CUTOFF, aggfunc="first")
        pivot = pivot.dropna(subset=list(self._YEARS))
        if pivot.empty:
            return {"status": "Insufficient Historical Data", "records": []}
        change = pivot[2025] - pivot[2024]
        records = pivot.reset_index().assign(cutoff_change=change.round(2))
        return {"status": "Historical comparison only", "records": records.to_dict(orient="records")}

    def get_branch_trends(self) -> list[dict[str, Any]]:
        cohorts = self._multi_year_cohorts()
        if cohorts.empty:
            return []
        trends = cohorts.groupby([COL_BRANCH, COL_YEAR], dropna=False).agg(avg_cutoff=(COL_CUTOFF, "mean"), cohort_count=(COL_COLLEGE_NAME, "size")).reset_index()
        return trends.sort_values([COL_YEAR, "avg_cutoff", COL_BRANCH], ascending=[False, False, True], kind="stable").to_dict(orient="records")

    def get_city_trends(self) -> list[dict[str, Any]]:
        cohorts = self._multi_year_cohorts()
        if cohorts.empty:
            return []
        trends = cohorts.groupby([COL_CITY, COL_YEAR], dropna=False).agg(avg_cutoff=(COL_CUTOFF, "mean"), cohort_count=(COL_COLLEGE_NAME, "size")).reset_index()
        return trends.sort_values([COL_YEAR, "avg_cutoff", COL_CITY], ascending=[False, False, True], kind="stable").to_dict(orient="records")

    def get_competition_analysis(self) -> list[dict[str, Any]]:
        cohorts = self._multi_year_cohorts()
        if cohorts.empty:
            return []
        analysis = cohorts.groupby([COL_BRANCH, COL_CITY], dropna=False).agg(avg_cutoff=(COL_CUTOFF, "mean"), cutoff_std=(COL_CUTOFF, "std"), cohort_count=(COL_COLLEGE_NAME, "size")).reset_index()
        analysis["cutoff_std"] = analysis["cutoff_std"].fillna(0.0)
        analysis["competition_score"] = np.where(analysis["cohort_count"].ge(2), analysis["avg_cutoff"] / (analysis["cutoff_std"] + 1.0), np.nan)
        analysis = analysis.dropna(subset=["competition_score"])
        return analysis.sort_values(["competition_score", "cohort_count"], ascending=[False, False], kind="stable").head(20).to_dict(orient="records")
