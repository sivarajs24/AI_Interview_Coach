import os
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interviewiq.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client

from coach.models import Candidate, InterviewReport, create_candidate_session


def test_user_registration_creates_account():
    username = f"alice_{uuid.uuid4().hex[:8]}"
    client = Client()
    response = client.post(
        "/accounts/register/",
        {
            "username": username,
            "email": f"{username}@example.com",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        },
    )

    assert response.status_code in (200, 302)
    assert response.wsgi_request.user.is_authenticated or response.status_code == 302


def test_login_page_renders():
    client = Client()
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    assert b"Login" in response.content


def test_dashboard_shows_user_sessions():
    username = f"carol_{uuid.uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="StrongPass123!")
    session = create_candidate_session(
        candidate_name="Carol Smith",
        target_role="Product Manager",
        llm_model="ollama/qwen3:4b",
        questions=[{"index": 0, "category": "Behavioral", "question": "Tell me about leadership."}],
        user=user,
    )
    InterviewReport.objects.create(
        session=session,
        overall_score=88.0,
        confidence_score=90.0,
        sentiment_score=82.0,
        stress_score=25.0,
        summary="Good control and clarity.",
        suggestions=["Keep speaking a bit slower."],
        raw_report={"scores": {"confidence_score": 90.0}},
    )

    client = Client()
    client.force_login(user)
    response = client.get("/dashboard/")

    assert response.status_code == 200
    assert b"Carol Smith" in response.content
    assert b"88.0" in response.content or b"88" in response.content


def test_user_profile_page_renders():
    username = f"dana_{uuid.uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="StrongPass123!")
    client = Client()
    client.force_login(user)
    response = client.get("/profile/")

    assert response.status_code == 200
    assert b"Profile" in response.content or b"My Profile" in response.content
    assert username.encode() in response.content


def test_candidate_management_page_lists_candidates():
    username = f"erin_{uuid.uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="StrongPass123!")
    Candidate.objects.create(user=user, full_name="Erin Walker", target_role="QA Engineer")

    client = Client()
    client.force_login(user)
    response = client.get("/candidates/")

    assert response.status_code == 200
    assert b"Erin Walker" in response.content
    assert b"Candidate Management" in response.content


def test_user_profile_edit_form_updates_user():
    username = f"frank_{uuid.uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, email=f"{username}@example.com", password="StrongPass123!")

    client = Client()
    client.force_login(user)
    response = client.post(
        "/profile/edit/",
        {"username": f"frank_updated_{uuid.uuid4().hex[:6]}", "email": "frank.updated@example.com"},
    )

    assert response.status_code in (200, 302)
    user.refresh_from_db()
    assert user.email == "frank.updated@example.com"


def test_candidate_crud_flow():
    username = f"gina_{uuid.uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="StrongPass123!")
    client = Client()
    client.force_login(user)

    create_response = client.post(
        "/candidates/add/",
        {"full_name": "Gina Flores", "target_role": "Product Designer"},
    )
    assert create_response.status_code in (200, 302)

    candidate = Candidate.objects.get(user=user, full_name="Gina Flores")
    update_response = client.post(
        f"/candidates/{candidate.id}/edit/",
        {"full_name": "Gina Flores", "target_role": "Senior Product Designer"},
    )
    assert update_response.status_code in (200, 302)
    candidate.refresh_from_db()
    assert candidate.target_role == "Senior Product Designer"

    delete_response = client.post(f"/candidates/{candidate.id}/delete/")
    assert delete_response.status_code in (200, 302)
    assert not Candidate.objects.filter(pk=candidate.pk).exists()


def test_report_page_contains_summary_and_suggestions():
    username = f"hannah_{uuid.uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="StrongPass123!")
    session = create_candidate_session(
        candidate_name="Hannah Lee",
        target_role="Engineering Manager",
        llm_model="ollama/qwen3:4b",
        questions=[{"index": 0, "category": "Leadership", "question": "Describe a challenge you led."}],
        user=user,
    )
    InterviewReport.objects.create(
        session=session,
        overall_score=91.0,
        confidence_score=94.0,
        sentiment_score=88.0,
        stress_score=20.0,
        summary="Strong leadership communication and clear examples.",
        suggestions=["Keep examples concise and measurable."],
        raw_report={"scores": {"confidence_score": 94.0}},
    )

    client = Client()
    client.force_login(user)
    response = client.get(f"/report/{session.session_key}")

    assert response.status_code == 200
    assert b"Strong leadership communication" in response.content
    assert b"Keep examples concise" in response.content


def test_candidate_detail_page_shows_history_and_summary():
    username = f"ivan_{uuid.uuid4().hex[:8]}"
    user = get_user_model().objects.create_user(username=username, password="StrongPass123!")
    candidate = Candidate.objects.create(user=user, full_name="Ivan Petrov", target_role="Data Analyst")
    session = create_candidate_session(
        candidate_name="Ivan Petrov",
        target_role="Data Analyst",
        llm_model="ollama/qwen3:4b",
        questions=[{"index": 0, "category": "Analytical", "question": "Tell me about a metric you improved."}],
        user=user,
    )
    session.candidate = candidate
    session.save(update_fields=["candidate"])
    InterviewReport.objects.create(
        session=session,
        overall_score=86.0,
        confidence_score=88.0,
        sentiment_score=84.0,
        stress_score=30.0,
        summary="Strong analytical thinking and structured story telling.",
        suggestions=["Use more business impact examples."],
        raw_report={"scores": {"confidence_score": 88.0}},
    )

    client = Client()
    client.force_login(user)
    response = client.get(f"/candidates/{candidate.id}/")

    assert response.status_code == 200
    assert b"Ivan Petrov" in response.content
    assert b"Session History" in response.content
    assert b"86.0" in response.content or b"86" in response.content
