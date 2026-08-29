"""Deterministic, metric-safe Diploma eligibility prediction engine."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

import pandas as pd

from cet_predictor import _predict_for_exam, _probability_frame
from data_loader import DataLoader

log = logging.getLogger("digipath.diploma_predictor")


class DiplomaPredictor:
    """Queries only declared DIPLOMA records; CET records are never reused."""

    def __init__(self, data_loader: DataLoader) -> None:
        self.data_loader = data_loader
        self.data = self.data_loader.get_combined_diploma_data()
        log.info("Diploma predictor initialized with %d canonical rows.", len(self.data))

    def calculate_probability(self, user_val: float, cutoff_val: float) -> tuple[str, int]:
        frame = _probability_frame(float(user_val), pd.Series([float(cutoff_val)]))
        return str(frame.iloc[0]["probability_label"]), int(frame.iloc[0]["probability_percent"])

    def predict(
        self,
        percentage: float,
        category: str,
        branch: Optional[str] = None,
        city: Optional[str] = None,
        college_type: Optional[str] = None,
        extra_filters: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        return _predict_for_exam(
            data_loader=self.data_loader,
            data=self.data_loader.get_combined_diploma_data(),
            exam_type="DIPLOMA",
            candidate_percentile=float(percentage),
            category=category,
            branch=branch,
            city=city,
            college_type=college_type,
            extra_filters=extra_filters,
        )
