from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from database import Base
import datetime

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, default="USER", index=True) # ADMIN, USER
    career_interests = Column(JSON, default=[]) # List of interests
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    predictions = relationship("Prediction", back_populates="user")
    resume_analyses = relationship("ResumeAnalysis", back_populates="user")
    scam_reports = relationship("ScamReport", back_populates="user")
    chat_histories = relationship("ChatHistory", back_populates="user")
    roadmaps = relationship("Roadmap", back_populates="user")

class Institute(Base):
    __tablename__ = "institutes"
    id = Column(Integer, primary_key=True, index=True)
    dte_code = Column(String, unique=True, index=True)
    name = Column(String, index=True)
    city = Column(String, index=True)
    district = Column(String)
    region = Column(String)
    type = Column(String) # Government, Private, Autonomous
    autonomous = Column(String)
    status = Column(String)
    website = Column(String)
    email = Column(String)
    phone = Column(String)
    address = Column(Text)
    placement_stats = Column(JSON) # {avg_package: 10, highest_package: 40, placement_rate: 90}
    facilities = Column(JSON) # [hostel, library, gym]

class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    score = Column(Float) # Percentile or Percentage
    exam_type = Column(String, index=True) # CET, DIPLOMA
    category = Column(String)
    branch_preference = Column(String)
    filters = Column(JSON) # Store applied filters
    results = Column(JSON) # List of recommended colleges
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="predictions")

class ResumeAnalysis(Base):
    __tablename__ = "resume_analyses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    filename = Column(String)
    
    # ATS Scores
    ats_score = Column(Float)
    skill_score = Column(Float)
    project_score = Column(Float)
    keyword_score = Column(Float)
    education_score = Column(Float)
    
    # Analysis results
    extracted_skills = Column(JSON)
    domain = Column(String)
    improvement_suggestions = Column(JSON)
    
    # Career Recommendations
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
    job_title = Column(String, index=True)
    company = Column(String)
    match_score = Column(Float)
    job_url = Column(String)
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
    scammer_entity = Column(String, index=True)
    platform = Column(String)
    scam_type = Column(String)
    description = Column(Text)
    evidence_url = Column(String)
    status = Column(String, default="PENDING", index=True) # PENDING, VERIFIED, REJECTED
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="scam_reports")

class Roadmap(Base):
    __tablename__ = "roadmaps"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    target_role = Column(String, index=True)
    steps = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="roadmaps")
