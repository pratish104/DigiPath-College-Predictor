"""Deterministic, metric-safe CET eligibility prediction engine."""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Optional

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
    COL_STATE_RANK,
    COL_TYPE,
    COL_YEAR,
    DataLoader,
    _normalize_branch,
)

log = logging.getLogger("digipath.cet_predictor")


def _literal_contains(values: pd.Series, query: str) -> pd.Series:
    return values.astype("string").str.contains(query, case=False, regex=False, na=False)


def _token_set_branch_match(values: pd.Series, branch: str) -> pd.Series:
    canonical_branch = _normalize_branch(branch) or branch
    tokens = re.findall(r"[a-z0-9]+", canonical_branch.casefold())
    if not tokens:
        return pd.Series(False, index=values.index)
    masks = [_literal_contains(values, token) for token in tokens]
    return pd.Series(np.logical_and.reduce([mask.to_numpy(dtype=bool) for mask in masks]), index=values.index)


def _probability_frame(candidate_percentile: float, cutoffs: pd.Series) -> pd.DataFrame:
    delta = candidate_percentile - cutoffs.astype(float)
    probability = np.select(
        [delta.ge(2.0), delta.ge(-1.5)],
        [np.clip(85.0 + (delta - 2.0) * 7.0, 85.0, 99.0), np.clip(50.0 + ((delta + 1.5) / 3.5) * 34.0, 50.0, 84.0)],
        default=np.clip(49.0 + (delta + 1.5) * 13.0, 10.0, 49.0),
    )
    chance = np.select([delta.ge(2.0), delta.ge(-1.5)], ["High Chance", "Moderate Chance"], default="Low / Dream Chance")
    classification = np.select([delta.ge(2.0), delta.ge(-1.5)], ["Safe", "Moderate"], default="Dream")
    return pd.DataFrame({"score_diff": delta.round(2), "probability_percent": np.rint(probability).astype(int), "probability_label": chance, "classification": classification}, index=cutoffs.index)


def _predict_for_exam(
    data_loader: DataLoader,
    data: pd.DataFrame,
    exam_type: str,
    candidate_percentile: float,
    category: str,
    branch: Optional[str] = None,
    city: Optional[str] = None,
    college_type: Optional[str] = None,
    extra_filters: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    if not np.isfinite(candidate_percentile) or not 0.0 <= candidate_percentile <= 100.0:
        return []
    frame = data.loc[data[COL_EXAM_TYPE].eq(exam_type)].copy()
    if frame.empty:
        return []

    normalized_category = str(category or "").strip()
    if normalized_category and normalized_category.casefold() != "all":
        frame = frame.loc[_literal_contains(frame[COL_CATEGORY], normalized_category)]
    if branch and branch.strip().casefold() != "all":
        frame = frame.loc[_token_set_branch_match(frame[COL_BRANCH], branch)]
    if city and city.strip().casefold() != "all":
        frame = frame.loc[_literal_contains(frame[COL_CITY], city.strip())]
    if college_type and college_type.strip().casefold() != "all":
        frame = frame.loc[_literal_contains(frame[COL_TYPE], college_type.strip())]
    if extra_filters:
        supported = {COL_QUOTA, COL_SEAT_TYPE}
        requested = {key: value for key, value in extra_filters.items() if key in supported and value is not None and str(value).strip().casefold() != "all"}
        filter_masks = [_literal_contains(frame[key], str(value).strip()) for key, value in requested.items()]
        if filter_masks:
            frame = frame.loc[pd.Series(np.logical_and.reduce([mask.to_numpy(dtype=bool) for mask in filter_masks]), index=frame.index)]
    if frame.empty:
        return []

    recommendation_grain = [COL_COLLEGE_CODE, COL_BRANCH, COL_CATEGORY, COL_QUOTA, COL_SEAT_TYPE, COL_EXAM_TYPE]
    frame = frame.sort_values([COL_YEAR, COL_CUTOFF], ascending=[False, False], kind="stable").drop_duplicates(subset=recommendation_grain, keep="first")
    probability = _probability_frame(candidate_percentile, frame[COL_CUTOFF])
    result = pd.concat([frame, probability], axis=1)
    result["predicted_cutoff"] = np.nan
    result["forecast_status"] = "No forecast generated; historical cutoff shown."
    result["college_type"] = result[COL_TYPE]
    result["category_used"] = result[COL_CATEGORY]
    result["placement_score"] = 0.0
    result = result.sort_values(["probability_percent", "score_diff", COL_CUTOFF, COL_COLLEGE_NAME], ascending=[False, False, False, True], kind="stable").reset_index(drop=True)
    result["rank"] = result.index + 1
    output_columns = [
        "rank", COL_COLLEGE_NAME, COL_COLLEGE_CODE, COL_BRANCH, COL_CITY, "college_type", "category_used",
        COL_QUOTA, COL_SEAT_TYPE, COL_CUTOFF, "predicted_cutoff", "forecast_status", "probability_label",
        "probability_percent", "classification", "score_diff", "placement_score", COL_YEAR, COL_STATE_RANK,
    ]
    result = result.loc[:, output_columns].replace({np.nan: None, pd.NA: None})
    return result.to_dict(orient="records")


class CETPredictor:
    """Returns deterministic CET eligibility bands from validated percentile history."""

    def __init__(self, data_loader: DataLoader) -> None:
        self.data_loader = data_loader
        self.data = self.data_loader.get_combined_cet_data()
        log.info("CET predictor initialized with %d canonical rows.", len(self.data))

    def calculate_probability(self, user_val: float, cutoff_val: float) -> tuple[str, int]:
        frame = _probability_frame(float(user_val), pd.Series([float(cutoff_val)]))
        return str(frame.iloc[0]["probability_label"]), int(frame.iloc[0]["probability_percent"])

    def predict(
        self,
        percentile: float,
        category: str,
        branch: Optional[str] = None,
        city: Optional[str] = None,
        college_type: Optional[str] = None,
        extra_filters: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        return _predict_for_exam(
            data_loader=self.data_loader,
            data=self.data_loader.get_combined_cet_data(),
            exam_type="CET",
            candidate_percentile=float(percentile),
            category=category,
            branch=branch,
            city=city,
            college_type=college_type,
            extra_filters=extra_filters,
        )
