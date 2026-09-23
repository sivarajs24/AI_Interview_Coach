from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from database import get_db
from auth import require_auth
from models import User, InterviewSession, InterviewQuestion, InterviewResponse, Candidate
import uuid
from datetime import datetime, timezone
import math
import os
import tempfile
import json
from pathlib import Path

from modules.interview_engine import InterviewEngine
from modules.report_generator import ReportGenerator

def get_interview_engine():
    return InterviewEngine()
    
def get_report_generator():
    return ReportGenerator()

router = APIRouter()

def create_candidate_session(candidate_name, target_role, llm_model, questions, session_key, user, db):
    full_name = (candidate_name or "Candidate").strip() or "Candidate"
    first_name = ""
    last_name = ""
    if " " in full_name:
        first_name, last_name = full_name.split(" ", 1)
    else:
        first_name = full_name
        
    candidate = None
    if user:
        candidate = db.query(Candidate).filter(Candidate.user_id == user.id, Candidate.full_name == full_name).first()
    
    if not candidate:
        candidate = Candidate(
            user_id=user.id if user else None,
            full_name=full_name,
            first_name=first_name,
            last_name=last_name,
            target_role=target_role
        )
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        
    session = InterviewSession(
        session_key=session_key or str(uuid.uuid4().hex),
        user_id=user.id if user else None,
        candidate_id=candidate.id,
        target_role=target_role,
        status="created",
        ai_model=llm_model or "ollama/qwen3:4b",
        started_at=datetime.now(timezone.utc)
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    
    for item in questions or []:
        question_index = int(item.get("index", 0))
        category = str(item.get("category", "General")).strip() or "General"
        question_text = str(item.get("question", "")).strip()
        if not question_text:
            continue
            
        iq = InterviewQuestion(
            session_id=session.id,
            index=question_index,
            category=category,
            question_text=question_text,
            rewritten_text=item.get("question", "") or question_text
        )
        db.add(iq)
    db.commit()
    return session

@router.post("/start-session")
async def start_session(request: Request, db: Session = Depends(get_db)):
    # Note: user might be none if not using token, but we are using require_auth in real app if we want.
    # The original view had @login_required, but handled request.user.is_authenticated.
    from auth import get_current_user
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"success": False, "error": {"code": "UNAUTHORIZED", "message": "Login required"}}, status_code=401)
        
    try:
        data = await request.json()
    except Exception:
        data = {}
        
    candidate_name = str(data.get("candidate_name", "Candidate")).strip() or "Candidate"
    target_role = str(data.get("target_role", "Software Engineer")).strip() or "Software Engineer"
    
    interview_engine = get_interview_engine()
    session_id = uuid.uuid4().hex
    questions = interview_engine.build_question_sequence(candidate_name=candidate_name, target_role=target_role)

    saved_session = create_candidate_session(
        candidate_name=candidate_name,
        target_role=target_role,
        llm_model=interview_engine.active_llm_model() or "ollama/qwen3:4b",
        questions=questions,
        session_key=session_id,
        user=user,
        db=db
    )

    return JSONResponse({
        "success": True,
        "session_id": saved_session.session_key,
        "candidate_name": candidate_name,
        "target_role": target_role,
        "questions": questions,
        "total_questions": len(questions),
        "llm_model": interview_engine.active_llm_model(),
    })

def process_interview_response_task(response_id: int):
    # Generate report asynchronously in the background
    # Normally this would be a separate file/module
    from database import SessionLocal
    from models import InterviewResponse
    from modules.facial_analyzer import FacialAnalyzer
    from modules.sentiment_analyzer import SentimentAnalyzer
    from modules.whisper_transcriber import WhisperTranscriber
    
    db = SessionLocal()
    try:
        response = db.query(InterviewResponse).filter(InterviewResponse.id == response_id).first()
        if not response:
            return
            
        transcriber = WhisperTranscriber()
        sentiment_analyzer = SentimentAnalyzer(model_name="cardiffnlp/twitter-roberta-base-sentiment-latest")
        facial_analyzer = FacialAnalyzer()
        
        # 1. Transcribe Audio
        transcription = transcriber.transcribe(response.audio_path)
        transcript_text = str(transcription.get("text", "")).strip() or "Transcription unavailable"
        
        # 2. Sentiment Analysis
        if transcript_text == "Transcription unavailable":
            sentiment = sentiment_analyzer.empty_result()
        else:
            sentiment = sentiment_analyzer.analyze(transcript_text)
            
        # 3. Facial Emotion Analysis
        emotions = facial_analyzer.analyze_video(response.video_path)
        
        response.transcript = transcript_text
        response.transcription_data = transcription
        response.sentiment_data = sentiment
        response.emotion_data = emotions
        response.status = "completed"
        db.commit()
    except Exception as e:
        print(f"Error processing response: {e}")
        if response:
            response.status = "error"
            db.commit()
    finally:
        db.close()

@router.post("/upload-response")
async def upload_response(
    background_tasks: BackgroundTasks,
    session_id: str = Form(...), 
    question_index: int = Form(...), 
    duration_seconds: float = Form(0.0),
    audio: UploadFile = File(...),
    video: UploadFile = File(...),
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    session = db.query(InterviewSession).filter(InterviewSession.session_key == session_id).first()
    if not session:
        return JSONResponse({"success": False, "error": {"code": "SESSION_NOT_FOUND", "message": "Session not found"}}, status_code=404)
        
    if session.user_id != user.id:
        return JSONResponse({"success": False, "error": {"code": "SESSION_FORBIDDEN", "message": "Forbidden"}}, status_code=403)
        
    os.makedirs("uploads", exist_ok=True)
    audio_path = f"uploads/{session_id}_q{question_index}_audio.webm"
    video_path = f"uploads/{session_id}_q{question_index}_video.webm"
    
    with open(audio_path, "wb") as f:
        f.write(await audio.read())
    with open(video_path, "wb") as f:
        f.write(await video.read())
        
    if duration_seconds <= 0:
        try:
            import librosa
            duration_seconds = float(librosa.get_duration(path=audio_path))
        except:
            pass
            
    response_record = db.query(InterviewResponse).filter(
        InterviewResponse.session_id == session.id,
        InterviewResponse.question_index == question_index
    ).first()
    
    if not response_record:
        response_record = InterviewResponse(
            session_id=session.id,
            question_index=question_index,
        )
        db.add(response_record)
        
    response_record.duration_seconds = duration_seconds
    response_record.audio_path = audio_path
    response_record.video_path = video_path
    response_record.status = "processing"
    db.commit()
    db.refresh(response_record)
    
    background_tasks.add_task(process_interview_response_task, response_record.id)
    
    return JSONResponse({
        "success": True,
        "question_index": question_index,
        "status": "processing",
        "response_id": response_record.id
    }, status_code=202)

@router.get("/response_status/{session_id}/{question_index}/")
async def check_response_status(session_id: str, question_index: int, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    session = db.query(InterviewSession).filter(InterviewSession.session_key == session_id).first()
    if not session or session.user_id != user.id:
        return JSONResponse({"success": False, "error": {"code": "NOT_FOUND", "message": "Not found"}}, status_code=404)
        
    response = db.query(InterviewResponse).filter(InterviewResponse.session_id == session.id, InterviewResponse.question_index == question_index).first()
    if not response:
        return JSONResponse({"success": False, "error": {"code": "NOT_FOUND", "message": "Not found"}}, status_code=404)
        
    if response.status != "completed":
        return JSONResponse({"success": True, "status": response.status})
        
    distribution = response.emotion_data.get("emotion_distribution", {}) if response.emotion_data else {}
    dominant_emotion = max(distribution, key=distribution.get) if distribution else "unknown"
    return JSONResponse({
        "success": True,
        "status": response.status,
        "question_index": question_index,
        "duration_seconds": response.duration_seconds,
        "transcript": response.transcript,
        "sentiment": response.sentiment_data,
        "emotions": response.emotion_data,
        "dominant_emotion": dominant_emotion,
    })

def _generate_report(session_id: str, db: Session):
    session = db.query(InterviewSession).filter(InterviewSession.session_key == session_id).first()
    if not session:
        return None
        
    questions_records = db.query(InterviewQuestion).filter(InterviewQuestion.session_id == session.id).order_by(InterviewQuestion.index).all()
    questions = [{"index": q.index, "category": q.category, "question": q.rewritten_text or q.question_text} for q in questions_records]
    
    responses_records = db.query(InterviewResponse).filter(InterviewResponse.session_id == session.id).all()
    responses_dict = {r.question_index: r for r in responses_records}
    
    ordered_responses = []
    for question in questions:
        q_index = question["index"]
        resp = responses_dict.get(q_index)
        resp_data = {}
        if resp and resp.status == "completed":
            resp_data = {
                "transcript": resp.transcript,
                "sentiment": resp.sentiment_data,
                "emotions": resp.emotion_data,
                "duration_seconds": resp.duration_seconds
            }
        ordered_responses.append({
            "question_index": q_index,
            "category": question["category"],
            "question": question["question"],
            "response": resp_data
        })
        
    candidate = db.query(Candidate).filter(Candidate.id == session.candidate_id).first()
    candidate_name = candidate.full_name if candidate else "Candidate"
        
    session_payload = {
        "session_id": session.session_key,
        "candidate_name": candidate_name,
        "target_role": session.target_role,
        "created_at": session.started_at.isoformat() if session.started_at else "",
        "questions": questions,
        "responses": ordered_responses,
        "report": None
    }
    
    report_payload = get_report_generator().generate_report(session_payload)

    if session:
        score_summary = report_payload.get("scores", {}) or {}
        suggestions = report_payload.get("suggestions", []) or []
        overall_score = float(score_summary.get("confidence_score", 0.0) or 0.0)
        
        from models import InterviewReport
        report = db.query(InterviewReport).filter(InterviewReport.session_id == session.id).first()
        if not report:
            report = InterviewReport(session_id=session.id)
            db.add(report)
            
        report.overall_score = overall_score
        report.confidence_score = float(score_summary.get("confidence_score", 0.0) or 0.0)
        report.sentiment_score = float(score_summary.get("sentiment_score", 0.0) or 0.0)
        report.stress_score = float(score_summary.get("stress_score", 0.0) or 0.0)
        report.summary = report_payload.get("meta", {}).get("candidate_name", "") or "Interview completed."
        report.suggestions = suggestions
        report.raw_report = report_payload
        db.commit()
    return report_payload

@router.post("/finish-interview")
async def finish_interview(request: Request, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = str(data.get("session_id", "")).strip()
    
    session = db.query(InterviewSession).filter(InterviewSession.session_key == session_id).first()
    if not session or session.user_id != user.id:
        return JSONResponse({"success": False, "error": {"code": "NOT_FOUND", "message": "Not found"}}, status_code=404)
        
    report_payload = _generate_report(session_id, db)
    if not report_payload:
        return JSONResponse({"success": False, "error": {"code": "REPORT_FAILED", "message": "Failed"}}, status_code=500)
        
    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    db.commit()
    
    return JSONResponse({"success": True, "session_id": session_id, "report_url": f"/report/{session_id}", "summary": report_payload.get("scores", {})})

@router.get("/report/{session_id}")
async def get_report(session_id: str, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    report_payload = _generate_report(session_id, db)
    if not report_payload:
        return JSONResponse({"success": False, "error": {"code": "NOT_FOUND", "message": "Not found"}}, status_code=404)
    return JSONResponse({"success": True, "session_id": session_id, "report": report_payload})

def _build_pdf(report_payload, session_id):
    import io
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

@router.get("/report/{session_id}/pdf")
async def download_report_pdf(session_id: str, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    report_payload = _generate_report(session_id, db)
    if not report_payload:
        return JSONResponse({"success": False, "error": {"code": "NOT_FOUND", "message": "Not found"}}, status_code=404)
    buffer = _build_pdf(report_payload, session_id)
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
        f.write(buffer.getvalue())
        tmp_path = f.name
    return FileResponse(tmp_path, media_type="application/pdf", filename=f"AI_Interview_Coach_Report_{session_id}.pdf")

