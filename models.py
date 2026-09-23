import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime, Text, JSON, Boolean
from sqlalchemy.orm import relationship
from database import Base

def get_utc_now():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String)
    
    candidates = relationship("Candidate", back_populates="user")
    sessions = relationship("InterviewSession", back_populates="user")


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    first_name = Column(String, default="")
    last_name = Column(String, default="")
    full_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    current_role = Column(String, default="")
    target_role = Column(String, default="")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

    user = relationship("User", back_populates="candidates")
    sessions = relationship("InterviewSession", back_populates="candidate", cascade="all, delete-orphan")

    @property
    def display_name(self):
        if self.full_name.strip():
            return self.full_name.strip()
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts).strip() or "Unnamed candidate"


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_key = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4().hex))
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False)
    target_role = Column(String, default="")
    status = Column(String, default="created")
    ai_model = Column(String, default="ollama/qwen3:4b")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=get_utc_now)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)
    notes = Column(Text, default="")

    user = relationship("User", back_populates="sessions")
    candidate = relationship("Candidate", back_populates="sessions")
    questions = relationship("InterviewQuestion", back_populates="session", cascade="all, delete-orphan")
    responses = relationship("InterviewResponse", back_populates="session", cascade="all, delete-orphan")
    report = relationship("InterviewReport", back_populates="session", uselist=False, cascade="all, delete-orphan")


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("interview_sessions.id"), nullable=False)
    index = Column(Integer, nullable=False)
    category = Column(String, nullable=False)
    question_text = Column(Text, nullable=False)
    rewritten_text = Column(Text, default="")
    created_at = Column(DateTime, default=get_utc_now)

    session = relationship("InterviewSession", back_populates="questions")


class InterviewResponse(Base):
    __tablename__ = "interview_responses"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("interview_sessions.id"), nullable=False)
    question_index = Column(Integer, nullable=False)
    duration_seconds = Column(Float, default=0.0)
    transcript = Column(Text, default="")
    transcription_data = Column(JSON, default=dict)
    sentiment_data = Column(JSON, default=dict)
    emotion_data = Column(JSON, default=dict)
    audio_path = Column(String, default="")
    video_path = Column(String, default="")
    status = Column(String, default="processing")
    created_at = Column(DateTime, default=get_utc_now)

    session = relationship("InterviewSession", back_populates="responses")


class InterviewReport(Base):
    __tablename__ = "interview_reports"

    id = Column(String, primary_key=True, default=generate_uuid)
    session_id = Column(String, ForeignKey("interview_sessions.id"), unique=True, nullable=False)
    overall_score = Column(Float, default=0.0)
    confidence_score = Column(Float, default=0.0)
    sentiment_score = Column(Float, default=0.0)
    stress_score = Column(Float, default=0.0)
    summary = Column(Text, default="")
    suggestions = Column(JSON, default=list)
    raw_report = Column(JSON, default=dict)
    pdf_file = Column(String, nullable=True)
    generated_at = Column(DateTime, default=get_utc_now)

    session = relationship("InterviewSession", back_populates="report")
