"""Database models for storing candidate, interview, question, and report data."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db import models


class Candidate(models.Model):
    """Represents a single interview candidate."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="candidates", null=True, blank=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    full_name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    current_role = models.CharField(max_length=150, blank=True)
    target_role = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def display_name(self) -> str:
        if self.full_name.strip():
            return self.full_name.strip()
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    def __str__(self) -> str:
        return self.display_name or "Unnamed candidate"


class InterviewSession(models.Model):
    """Represents one interview session for a candidate."""

    STATUS_CREATED = "created"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_key = models.CharField(max_length=64, unique=True, db_index=True, blank=True, default="")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="interview_sessions", null=True, blank=True)
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="sessions")
    target_role = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CREATED)
    ai_model = models.CharField(max_length=120, blank=True, default="ollama/qwen3:4b")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.candidate.display_name} - {self.created_at.date()}"


class InterviewQuestion(models.Model):
    """Stores a single question within a session."""

    session = models.ForeignKey(InterviewSession, on_delete=models.CASCADE, related_name="questions")
    index = models.PositiveIntegerField()
    category = models.CharField(max_length=80)
    question_text = models.TextField()
    rewritten_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["session", "index"]
        constraints = [models.UniqueConstraint(fields=["session", "index"], name="unique_session_question_index")]

    def __str__(self) -> str:
        return f"{self.session_id} Q{self.index}: {self.category}"


class InterviewReport(models.Model):
    """Stores the final interview evaluation report for a session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField(InterviewSession, on_delete=models.CASCADE, related_name="report_detail")
    overall_score = models.FloatField(default=0.0)
    confidence_score = models.FloatField(default=0.0)
    sentiment_score = models.FloatField(default=0.0)
    stress_score = models.FloatField(default=0.0)
    summary = models.TextField(blank=True)
    suggestions = models.JSONField(default=list, blank=True)
    raw_report = models.JSONField(default=dict, blank=True)
    pdf_file = models.FileField(upload_to="reports/", blank=True, null=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self) -> str:
        return f"Report for {self.session}"


def create_candidate_session(
    candidate_name: str,
    target_role: str,
    llm_model: str,
    questions: Optional[List[Dict[str, Any]]] = None,
    session_key: Optional[str] = None,
    user: Optional[settings.AUTH_USER_MODEL] = None,
) -> InterviewSession:
    """Create the DB-backed candidate and interview session, including persisted questions."""
    full_name = (candidate_name or "Candidate").strip() or "Candidate"
    first_name = ""
    last_name = ""
    if " " in full_name:
        first_name, last_name = full_name.split(" ", 1)
    else:
        first_name = full_name

    candidate, _ = Candidate.objects.get_or_create(
        user=user,
        full_name=full_name,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "target_role": target_role,
        },
    )
    session = InterviewSession.objects.create(
        user=user,
        candidate=candidate,
        target_role=target_role,
        status=InterviewSession.STATUS_CREATED,
        ai_model=llm_model or "ollama/qwen3:4b",
        session_key=session_key or uuid.uuid4().hex,
        started_at=datetime.now(timezone.utc),
    )

    for item in questions or []:
        question_index = int(item.get("index", 0))
        category = str(item.get("category", "General")).strip() or "General"
        question_text = str(item.get("question", "")).strip()
        if not question_text:
            continue
        InterviewQuestion.objects.update_or_create(
            session=session,
            index=question_index,
            defaults={
                "category": category,
                "question_text": question_text,
                "rewritten_text": item.get("question", "") or question_text,
            },
        )

    return session


def save_report_for_session(session_key: str, report_payload: Dict[str, Any]) -> Optional[InterviewReport]:
    """Persist the generated report payload to the database for the session."""
    try:
        session = InterviewSession.objects.get(session_key=session_key)
    except InterviewSession.DoesNotExist:
        return None

    score_summary = report_payload.get("scores", {}) or {}
    suggestions = report_payload.get("suggestions", []) or []
    overall_score = float(score_summary.get("confidence_score", 0.0) or 0.0)

    report, _ = InterviewReport.objects.update_or_create(
        session=session,
        defaults={
            "overall_score": overall_score,
            "confidence_score": float(score_summary.get("confidence_score", 0.0) or 0.0),
            "sentiment_score": float(score_summary.get("sentiment_score", 0.0) or 0.0),
            "stress_score": float(score_summary.get("stress_score", 0.0) or 0.0),
            "summary": report_payload.get("meta", {}).get("candidate_name", "") or "Interview completed.",
            "suggestions": suggestions,
            "raw_report": report_payload,
        },
    )
    return report
