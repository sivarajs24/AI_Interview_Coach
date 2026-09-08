"""Django HTTP views for AI Interview Coach."""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Avg, Count, Q
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from .models import Candidate, InterviewReport

from .models import create_candidate_session, save_report_for_session
from .services import (
    get_facial_analyzer,
    get_interview_engine,
    get_report_generator,
    get_sentiment_analyzer,
    get_transcriber,
)


def _json_error(message: str, status_code: int = 400, code: str = "BAD_REQUEST") -> JsonResponse:
    return JsonResponse({"success": False, "error": {"code": code, "message": message}}, status=status_code)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _save_upload(uploaded_file: Any, prefix: str, fallback_ext: str) -> Path:
    candidate = Path(uploaded_file.name or "").suffix.strip().lower()
    extension = candidate if candidate and len(candidate) <= 8 else fallback_ext
    output_path = settings.MEDIA_ROOT / f"{prefix}_{uuid.uuid4().hex}{extension}"
    with output_path.open("wb+") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)
    return output_path


def _build_pdf(report_payload: Dict[str, Any], session_id: str) -> io.BytesIO:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=letter, title="AI Interview Coach Report")
    styles = getSampleStyleSheet()
    story = [
        Paragraph("AI Interview Coach - Interview Report", styles["Title"]),
        Paragraph(f"Session ID: {session_id}", styles["Normal"]),
        Paragraph(f"Generated: {datetime.now(timezone.utc).isoformat()}", styles["Normal"]),
        Spacer(1, 12),
    ]
    scores = report_payload.get("scores", {})
    for label, key in (("Confidence", "confidence_score"), ("Sentiment", "sentiment_score"), ("Stress", "stress_score")):
        story.append(Paragraph(f"{label} Score: {scores.get(key, 0):.1f}", styles["Heading3"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Actionable Suggestions", styles["Heading2"]))
    for index, suggestion in enumerate(report_payload.get("suggestions", []), start=1):
        story.extend([Paragraph(f"{index}. {suggestion}", styles["Normal"]), Spacer(1, 6)])
    document.build(story)
    buffer.seek(0)
    return buffer


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "index.html", {"title": "AI Interview Coach | AI Interview Coach"})


@login_required
def dashboard_view(request: HttpRequest) -> HttpResponse:
    sessions = request.user.interview_sessions.select_related("candidate").order_by("-created_at")
    report_scores = []
    for session in sessions:
        report = getattr(session, "report_detail", None)
        if report:
            report_scores.append(report.overall_score)

    average_score = round(sum(report_scores) / len(report_scores), 1) if report_scores else 0.0
    completed_sessions = sessions.filter(status="completed").count()
    total_candidates = request.user.candidates.count()

    return render(
        request,
        "dashboard.html",
        {
            "title": "Dashboard",
            "sessions": sessions,
            "average_score": average_score,
            "completed_sessions": completed_sessions,
            "total_candidates": total_candidates,
        },
    )


@login_required
def profile_view(request: HttpRequest) -> HttpResponse:
    session_count = request.user.interview_sessions.count()
    completed_count = request.user.interview_sessions.filter(status="completed").count()
    latest_report = (
        InterviewReport.objects.filter(session__user=request.user)
        .select_related("session", "session__candidate")
        .order_by("-generated_at")
        .first()
    )
    return render(
        request,
        "profile.html",
        {
            "title": "My Profile",
            "session_count": session_count,
            "completed_count": completed_count,
            "latest_report": latest_report,
            "user": request.user,
        },
    )


@login_required
def candidate_management_view(request: HttpRequest) -> HttpResponse:
    candidates = request.user.candidates.order_by("-created_at")
    return render(request, "candidate_management.html", {"title": "Candidate Management", "candidates": candidates})


@login_required
def candidate_detail_view(request: HttpRequest, candidate_id: str) -> HttpResponse:
    candidate = get_object_or_404(Candidate, pk=candidate_id, user=request.user)
    sessions = candidate.sessions.select_related("report_detail").order_by("-created_at")
    scores = [float(session.report_detail.overall_score) for session in sessions if getattr(session, "report_detail", None) is not None]
    average_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    latest_report = None
    if sessions:
        for session in sessions:
            if getattr(session, "report_detail", None) is not None:
                latest_report = session.report_detail
                break

    return render(
        request,
        "candidate_detail.html",
        {
            "title": f"{candidate.display_name} | Candidate Detail",
            "candidate": candidate,
            "sessions": sessions,
            "average_score": average_score,
            "latest_report": latest_report,
            "session_count": sessions.count(),
        },
    )


@login_required
def profile_edit_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        if not username:
            return render(request, "profile_edit.html", {"title": "Edit Profile", "error": "Username is required.", "user": request.user})

        user_model = settings.AUTH_USER_MODEL
        existing_user = getattr(__import__("django.contrib.auth", fromlist=["get_user_model"]).get_user_model(), "objects", None)
        if existing_user is not None:
            duplicate = existing_user.filter(username=username).exclude(pk=request.user.pk).exists()
            if duplicate:
                return render(request, "profile_edit.html", {"title": "Edit Profile", "error": "That username is already taken.", "user": request.user})

        request.user.username = username
        request.user.email = email
        request.user.save(update_fields=["username", "email"])
        messages.success(request, "Your profile was updated successfully.")
        return redirect("profile")

    return render(request, "profile_edit.html", {"title": "Edit Profile", "user": request.user})


@login_required
def candidate_add_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        full_name = (request.POST.get("full_name") or "").strip()
        target_role = (request.POST.get("target_role") or "").strip()
        email = (request.POST.get("email") or "").strip()
        if not full_name:
            return render(request, "candidate_form.html", {"title": "Add Candidate", "error": "Full name is required."})

        Candidate.objects.create(
            user=request.user,
            full_name=full_name,
            target_role=target_role,
            email=email or None,
        )
        messages.success(request, "Candidate added successfully.")
        return redirect("candidate_management")

    return render(request, "candidate_form.html", {"title": "Add Candidate", "candidate": None})


@login_required
def candidate_edit_view(request: HttpRequest, candidate_id: str) -> HttpResponse:
    candidate = get_object_or_404(Candidate, pk=candidate_id, user=request.user)
    if request.method == "POST":
        candidate.full_name = (request.POST.get("full_name") or "").strip() or candidate.full_name
        candidate.target_role = (request.POST.get("target_role") or "").strip()
        candidate.email = (request.POST.get("email") or "").strip() or None
        candidate.notes = (request.POST.get("notes") or "").strip()
        candidate.save(update_fields=["full_name", "target_role", "email", "notes", "updated_at"])
        messages.success(request, "Candidate updated successfully.")
        return redirect("candidate_management")

    return render(request, "candidate_form.html", {"title": "Edit Candidate", "candidate": candidate})


@login_required
def candidate_delete_view(request: HttpRequest, candidate_id: str) -> HttpResponse:
    candidate = get_object_or_404(Candidate, pk=candidate_id, user=request.user)
    if request.method == "POST":
        candidate.delete()
        messages.success(request, "Candidate deleted successfully.")
        return redirect("candidate_management")
    return redirect("candidate_management")


@login_required
def interview_page(request: HttpRequest) -> HttpResponse:
    return render(request, "interview.html", {"title": "AI Interview Coach | Interview Session"})


@login_required
def report_page(request: HttpRequest, session_id: str) -> HttpResponse:
    session = request.user.interview_sessions.select_related("candidate", "report_detail").filter(session_key=session_id).first()
    if session is None:
        return redirect("dashboard")
    report = session.report_detail
    report_data = report.raw_report if report else {}
    summary_text = report.summary if report else "Interview completed successfully."
    suggestions = report.suggestions if report else []
    return render(
        request,
        "report.html",
        {
            "title": "AI Interview Coach | Report",
            "session_id": session_id,
            "report": report,
            "report_data": report_data,
            "summary_text": summary_text,
            "suggestions": suggestions,
            "session": session,
        },
    )


def favicon(request: HttpRequest) -> HttpResponse:
    favicon_path = settings.BASE_DIR / "static" / "assets" / "logo.svg"
    return FileResponse(favicon_path.open("rb"), content_type="image/svg+xml")


def register_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Your account was created successfully.")
            return redirect("home")
    else:
        form = UserCreationForm()
    return render(request, "register.html", {"title": "Create Account", "form": form})


@login_required
@csrf_exempt
def start_session(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_error("Method not allowed.", status_code=405, code="METHOD_NOT_ALLOWED")
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        data = {}
    candidate_name = str(data.get("candidate_name", "Candidate")).strip() or "Candidate"
    target_role = str(data.get("target_role", "Software Engineer")).strip() or "Software Engineer"
    interview_engine = get_interview_engine()
    session = interview_engine.create_session(candidate_name=candidate_name, target_role=target_role)
    questions = interview_engine.get_questions(session.session_id)
    session.questions = questions

    saved_session = create_candidate_session(
        candidate_name=candidate_name,
        target_role=target_role,
        llm_model=interview_engine.active_llm_model() or "ollama/qwen3:4b",
        questions=questions,
        session_key=session.session_id,
        user=request.user if request.user.is_authenticated else None,
    )

    return JsonResponse({
        "success": True,
        "session_id": saved_session.session_key or session.session_id,
        "candidate_name": session.candidate_name,
        "target_role": session.target_role,
        "questions": questions,
        "total_questions": len(questions),
        "llm_model": interview_engine.active_llm_model(),
    })


@login_required
@csrf_exempt
def upload_response(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return _json_error("Method not allowed.", status_code=405, code="METHOD_NOT_ALLOWED")
    interview_engine = get_interview_engine()
    session_id = str(request.POST.get("session_id", "")).strip()
    question_index = _safe_int(request.POST.get("question_index"), -1)
    duration_seconds = _safe_float(request.POST.get("duration_seconds"), 0.0)
    if not session_id:
        return _json_error("session_id is required.", code="MISSING_SESSION")
    session = interview_engine.get_session(session_id)
    if session is not None and getattr(session, "user", None) is not None and session.user != request.user:
        return _json_error("You do not have access to this session.", status_code=403, code="SESSION_FORBIDDEN")
    if not interview_engine.session_exists(session_id):
        return _json_error("Session not found.", status_code=404, code="SESSION_NOT_FOUND")
    if question_index < 0:
        return _json_error("question_index is required.", code="MISSING_QUESTION_INDEX")
    if question_index >= len(interview_engine.get_questions(session_id)):
        return _json_error("question_index is out of range for this session.", status_code=422, code="QUESTION_INDEX_OUT_OF_RANGE")
    audio_file = request.FILES.get("audio")
    video_file = request.FILES.get("video")
    if not audio_file or not video_file:
        return _json_error("Both audio and video files are required.", code="MISSING_MEDIA")

    audio_path = _save_upload(audio_file, f"{session_id}_q{question_index}_audio", ".webm")
    video_path = _save_upload(video_file, f"{session_id}_q{question_index}_video", ".webm")
    if duration_seconds <= 0:
        try:
            import librosa

            duration_seconds = float(librosa.get_duration(path=str(audio_path)))
        except Exception:
            duration_seconds = 0.0
    if not np.isfinite(duration_seconds) or duration_seconds < 0:
        duration_seconds = 0.0
    duration_seconds = min(duration_seconds, 180.0)
    if duration_seconds < 3:
        return _json_error("Audio is too short. Please answer for at least 3 seconds.", status_code=422, code="AUDIO_TOO_SHORT")

    transcriber = get_transcriber()
    sentiment_analyzer = get_sentiment_analyzer()
    facial_analyzer = get_facial_analyzer()
    transcription = transcriber.transcribe(str(audio_path))
    transcript_text = str(transcription.get("text", "")).strip() or "Transcription unavailable"
    sentiment = sentiment_analyzer.empty_result() if transcript_text == "Transcription unavailable" else sentiment_analyzer.analyze(transcript_text)
    emotions = facial_analyzer.analyze_video(str(video_path))
    dominant_emotion = facial_analyzer.get_overall_dominant_emotion(emotions.get("emotion_distribution", {}))
    response_record = {
        "question_index": question_index,
        "duration_seconds": duration_seconds,
        "transcript": transcript_text,
        "transcription": transcription,
        "sentiment": sentiment,
        "emotions": emotions,
        "audio_path": str(audio_path),
        "video_path": str(video_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    interview_engine.record_response(session_id, question_index, response_record)
    return JsonResponse({
        "success": True,
        "question_index": question_index,
        "duration_seconds": duration_seconds,
        "transcript": transcript_text,
        "sentiment": sentiment,
        "emotions": emotions,
        "dominant_emotion": dominant_emotion,
    })


def _generate_report(session_id: str) -> Dict[str, Any] | None:
    interview_engine = get_interview_engine()
    if not interview_engine.session_exists(session_id):
        return None
    report_payload = get_report_generator().generate_report(interview_engine.session_to_dict(session_id))
    interview_engine.store_report(session_id, report_payload)

    session = interview_engine.get_session(session_id)
    if session is not None:
        try:
            save_report_for_session(session.session_id, report_payload)
        except Exception:
            pass

    return report_payload


@login_required
@csrf_exempt
def finish_interview(request: HttpRequest) -> JsonResponse:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        data = {}
    session_id = str(data.get("session_id", "")).strip()
    if not session_id:
        return _json_error("session_id is required.", code="MISSING_SESSION")
    session = get_interview_engine().get_session(session_id)
    if session is not None and getattr(session, "user", None) is not None and session.user != request.user:
        return _json_error("You do not have access to this session.", status_code=403, code="SESSION_FORBIDDEN")
    report_payload = _generate_report(session_id)
    if report_payload is None:
        return _json_error("Session not found.", status_code=404, code="SESSION_NOT_FOUND")
    return JsonResponse({"success": True, "session_id": session_id, "report_url": f"/report/{session_id}", "summary": report_payload.get("scores", {})})


@login_required
def get_report(request: HttpRequest, session_id: str) -> JsonResponse:
    report_payload = _generate_report(session_id)
    if report_payload is None:
        return _json_error("Session not found.", status_code=404, code="SESSION_NOT_FOUND")
    return JsonResponse({"success": True, "session_id": session_id, "report": report_payload})


@login_required
@login_required
def download_report_pdf(request: HttpRequest, session_id: str) -> HttpResponse:
    report_payload = _generate_report(session_id)
    if report_payload is None:
        return _json_error("Session not found.", status_code=404, code="SESSION_NOT_FOUND")
    response = FileResponse(_build_pdf(report_payload, session_id), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="AI Interview Coach_Report_{session_id}.pdf"'
    return response