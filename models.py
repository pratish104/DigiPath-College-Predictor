"""SQLAlchemy ORM models compatible with MySQL 8.x."""

import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(120), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    hashed_password = Column(String(512), nullable=False)
    role = Column(String(30), nullable=False, default="USER", index=True)
    admin_level = Column(Integer, nullable=False, default=0)
    credits_balance = Column(Integer, nullable=False, default=50)
    career_interests = Column(JSON, default=list)
    # Profile settings stored as JSON to avoid MySQL migration risk.
    # Schema: {roll_number, preferred_branch, category, target_city,
    #          saved_colleges: [...], saved_jobs: [...]}
    profile_settings = Column(JSON, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    predictions = relationship("Prediction", back_populates="user")
    resume_analyses = relationship("ResumeAnalysis", back_populates="user")
    scam_reports = relationship("ScamReport", back_populates="user")
    chat_histories = relationship("ChatHistory", back_populates="user")
    roadmaps = relationship("Roadmap", back_populates="user")
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    error_logs = relationship("SystemErrorLog", back_populates="user")


class Institute(Base):
    __tablename__ = "institutes"
    id = Column(Integer, primary_key=True, index=True)
    dte_code = Column(String(32), unique=True, index=True)
    name = Column(String(500), index=True)
    city = Column(String(150), index=True)
    district = Column(String(150))
    region = Column(String(150))
    type = Column(String(100))
    autonomous = Column(String(100))
    status = Column(String(100))
    website = Column(String(2048))
    email = Column(String(255))
    phone = Column(String(50))
    address = Column(Text)
    placement_stats = Column(JSON)
    facilities = Column(JSON)


class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    score = Column(Float)
    exam_type = Column(String(32), index=True)
    category = Column(String(64))
    branch_preference = Column(String(255))
    filters = Column(JSON)
    results = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    user = relationship("User", back_populates="predictions")


class ResumeAnalysis(Base):
    __tablename__ = "resume_analyses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    filename = Column(String(255))
    ats_score = Column(Float)
    skill_score = Column(Float)
    project_score = Column(Float)
    keyword_score = Column(Float)
    education_score = Column(Float)
    extracted_skills = Column(JSON)
    domain = Column(String(255))
    improvement_suggestions = Column(JSON)
    recommended_careers = Column(JSON)
    recommended_job_roles = Column(JSON)
    recommended_certifications = Column(JSON)
    recommended_higher_studies = Column(JSON)
    skill_gaps = Column(JSON)
    learning_roadmap = Column(JSON)
    industry_recommendations = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    user = relationship("User", back_populates="resume_analyses")


class JobRecommendation(Base):
    __tablename__ = "job_recommendations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    job_title = Column(String(255), index=True)
    company = Column(String(255))
    match_score = Column(Float)
    job_url = Column(String(2048))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class ChatHistory(Base):
    __tablename__ = "chat_histories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    query = Column(Text)
    response = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    user = relationship("User", back_populates="chat_histories")


class ScamReport(Base):
    __tablename__ = "scam_reports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    scammer_entity = Column(String(255), index=True)
    platform = Column(String(100))
    scam_type = Column(String(100))
    description = Column(Text)
    evidence_url = Column(String(2048))
    status = Column(String(32), default="PENDING", index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    user = relationship("User", back_populates="scam_reports")


class Roadmap(Base):
    __tablename__ = "roadmaps"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    target_role = Column(String(255), index=True)
    steps = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    user = relationship("User", back_populates="roadmaps")


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_id = Column(String(100), unique=True, nullable=False, index=True)
    amount = Column(Float, default=50.0, nullable=False)
    credits_added = Column(Integer, default=300, nullable=False)
    payment_status = Column(String(32), default="PENDING", nullable=False, index=True)  # PENDING, PAID, FAILED
    cf_payment_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="transactions")


class SystemErrorLog(Base):
    __tablename__ = "system_error_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    module_name = Column(String(64), nullable=False, index=True)  # PREDICTOR, CHAT_RAG, CASHFREE_PAYMENT
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    status = Column(String(32), default="UNRESOLVED", nullable=False, index=True)  # UNRESOLVED, RESOLVED
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="error_logs")