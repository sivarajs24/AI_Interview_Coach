import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interviewiq.settings")

import django

django.setup()

from coach.models import Candidate, InterviewReport, InterviewSession, create_candidate_session


def test_create_candidate_session_persists_payload():
    session = create_candidate_session(
        candidate_name="Alice Johnson",
        target_role="Senior Python Engineer",
        llm_model="ollama/qwen3:4b",
        questions=[
            {"index": 0, "category": "Technical", "question": "Describe Python concurrency."},
            {"index": 1, "category": "Behavioral", "question": "Tell me about a conflict."},
        ],
    )

    assert Candidate.objects.filter(full_name="Alice Johnson").exists()
    assert session.candidate.full_name == "Alice Johnson"
    assert InterviewSession.objects.filter(pk=session.pk).exists()
    assert session.questions.count() == 2
    assert session.questions.filter(index=0, category="Technical").exists()


def test_create_report_for_session():
    session = create_candidate_session(
        candidate_name="Bob Smith",
        target_role="Data Engineer",
        llm_model="ollama/qwen3:4b",
        questions=[{"index": 0, "category": "Technical", "question": "How do you build a pipeline?"}],
    )

    report = InterviewReport.objects.create(
        session=session,
        overall_score=87.0,
        confidence_score=90.0,
        sentiment_score=82.0,
        stress_score=28.0,
        summary="Strong technical communication.",
        suggestions=["Improve confidence on metrics."],
        raw_report={"scores": {"confidence_score": 90.0}},
    )

    assert report.session == session
    assert report.overall_score == 87.0
    assert report.suggestions == ["Improve confidence on metrics."]
