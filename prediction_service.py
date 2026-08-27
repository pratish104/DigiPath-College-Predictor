from sqlalchemy.orm import Session
import models
from datetime import datetime

class PredictionService:
    @staticmethod
    def save_prediction(db: Session, user_id: int, score: float, exam_type: str, category: str, branch_pref: str, results: list):
        new_prediction = models.Prediction(
            user_id=user_id,
            score=score,
            exam_type=exam_type,
            category=category,
            branch_preference=branch_pref,
            results=results,
            created_at=datetime.utcnow()
        )
        db.add(new_prediction)
        db.commit()
        db.refresh(new_prediction)
        return new_prediction

    @staticmethod
    def get_user_history(db: Session, user_id: int):
        return db.query(models.Prediction).filter(models.Prediction.user_id == user_id).order_by(models.Prediction.created_at.desc()).all()
