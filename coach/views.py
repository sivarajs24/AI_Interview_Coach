"""Django HTTP views for InterviewIQ."""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

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
    document = SimpleDocTemplate(buffer, pagesize=letter, title="InterviewIQ Report")
    styles = getSampleStyleSheet()
    story = [
        Paragraph("InterviewIQ - Interview Report", styles["Title"]),
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
    return render(request, "index.html", {"title": "InterviewIQ | AI Interview Coach"})


def interview_page(request: HttpRequest) -> HttpResponse:
    return render(request, "interview.html", {"title": "InterviewIQ | Interview Session"})


def report_page(request: HttpRequest, session_id: str) -> HttpResponse:
    return render(request, "report.html", {"title": "InterviewIQ | Report", "session_id": session_id})


def favicon(request: HttpRequest) -> HttpResponse:
    favicon_path = settings.BASE_DIR / "static" / "assets" / "logo.svg"
    return FileResponse(favicon_path.open("rb"), content_type="image/svg+xml")


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
    return JsonResponse({
        "success": True,
        "session_id": session.session_id,
        "candidate_name": session.candidate_name,
        "target_role": session.target_role,
        "questions": questions,
        "total_questions": len(questions),
        "llm_model": interview_engine.active_llm_model(),
    })


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
    return report_payload


@csrf_exempt
def finish_interview(request: HttpRequest) -> JsonResponse:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        data = {}
    session_id = str(data.get("session_id", "")).strip()
    if not session_id:
        return _json_error("session_id is required.", code="MISSING_SESSION")
    report_payload = _generate_report(session_id)
    if report_payload is None:
        return _json_error("Session not found.", status_code=404, code="SESSION_NOT_FOUND")
    return JsonResponse({"success": True, "session_id": session_id, "report_url": f"/report/{session_id}", "summary": report_payload.get("scores", {})})


def get_report(request: HttpRequest, session_id: str) -> JsonResponse:
    report_payload = _generate_report(session_id)
    if report_payload is None:
        return _json_error("Session not found.", status_code=404, code="SESSION_NOT_FOUND")
    return JsonResponse({"success": True, "session_id": session_id, "report": report_payload})


def download_report_pdf(request: HttpRequest, session_id: str) -> HttpResponse:
    report_payload = _generate_report(session_id)
    if report_payload is None:
        return _json_error("Session not found.", status_code=404, code="SESSION_NOT_FOUND")
    response = FileResponse(_build_pdf(report_payload, session_id), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="InterviewIQ_Report_{session_id}.pdf"'
    return response