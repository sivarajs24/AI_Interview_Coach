from fastapi import APIRouter, Request, Depends, Form, Response, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc
from database import get_db
from models import User, Candidate, InterviewSession, InterviewReport
from auth import get_current_user, require_auth, verify_password, get_password_hash, create_access_token
import os
from pathlib import Path

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# We will use standard FastAPI Request object in templates

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "user": user, "title": "AI Interview Coach | AI Interview Coach"})

@router.get("/accounts/login/", response_class=HTMLResponse)
async def login_view(request: Request):
    return templates.TemplateResponse(request=request, name="registration/login.html", context={"request": request, "user": None, "title": "Login"})

@router.post("/accounts/login/")
async def login_post(request: Request, response: Response, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(request=request, name="registration/login.html", context={"request": request, "user": None, "error": "Invalid username or password", "title": "Login"})
    
    token = create_access_token(user.id)
    response = RedirectResponse(url="/dashboard/", status_code=303)
    response.set_cookie(key="session_token", value=token, httponly=True)
    return response

@router.get("/accounts/register/", response_class=HTMLResponse)
async def register_view(request: Request):
    return templates.TemplateResponse(request=request, name="register.html", context={"request": request, "user": None, "title": "Create Account"})

@router.post("/accounts/register/")
async def register_post(request: Request, username: str = Form(...), email: str = Form(""), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        return templates.TemplateResponse(request=request, name="register.html", context={"request": request, "user": None, "error": "Username already exists.", "title": "Create Account"})
    
    new_user = User(username=username, email=email, hashed_password=get_password_hash(password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    token = create_access_token(new_user.id)
    response = RedirectResponse(url="/dashboard/", status_code=303)
    response.set_cookie(key="session_token", value=token, httponly=True)
    return response

@router.get("/accounts/logout/")
async def logout_view():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_token")
    return response

@router.get("/dashboard/", response_class=HTMLResponse)
async def dashboard_view(request: Request, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    sessions = db.query(InterviewSession).filter(InterviewSession.user_id == user.id).order_by(desc(InterviewSession.created_at)).all()
    
    report_scores = []
    for session in sessions:
        if session.report:
            report_scores.append(session.report.overall_score)
            
    average_score = round(sum(report_scores) / len(report_scores), 1) if report_scores else 0.0
    completed_sessions = sum(1 for s in sessions if s.status == "completed")
    total_candidates = db.query(Candidate).filter(Candidate.user_id == user.id).count()

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "request": request,
        "user": user,
        "title": "Dashboard",
        "sessions": sessions,
        "average_score": average_score,
        "completed_sessions": completed_sessions,
        "total_candidates": total_candidates
    })

@router.get("/profile/", response_class=HTMLResponse)
async def profile_view(request: Request, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    session_count = db.query(InterviewSession).filter(InterviewSession.user_id == user.id).count()
    completed_count = db.query(InterviewSession).filter(InterviewSession.user_id == user.id, InterviewSession.status == "completed").count()
    latest_report = db.query(InterviewReport).join(InterviewSession).filter(InterviewSession.user_id == user.id).order_by(desc(InterviewReport.generated_at)).first()
    
    return templates.TemplateResponse(request=request, name="profile.html", context={
        "request": request,
        "user": user,
        "title": "My Profile",
        "session_count": session_count,
        "completed_count": completed_count,
        "latest_report": latest_report
    })

@router.get("/profile/edit/", response_class=HTMLResponse)
async def profile_edit_view(request: Request, user: User = Depends(require_auth)):
    return templates.TemplateResponse(request=request, name="profile_edit.html", context={"request": request, "user": user, "title": "Edit Profile"})

@router.post("/profile/edit/")
async def profile_edit_post(request: Request, username: str = Form(...), email: str = Form(""), user: User = Depends(require_auth), db: Session = Depends(get_db)):
    if not username.strip():
        return templates.TemplateResponse(request=request, name="profile_edit.html", context={"request": request, "user": user, "error": "Username is required.", "title": "Edit Profile"})
    
    duplicate = db.query(User).filter(User.username == username.strip(), User.id != user.id).first()
    if duplicate:
        return templates.TemplateResponse(request=request, name="profile_edit.html", context={"request": request, "user": user, "error": "That username is already taken.", "title": "Edit Profile"})
        
    user.username = username.strip()
    user.email = email.strip()
    db.commit()
    return RedirectResponse(url="/profile/", status_code=303)

@router.get("/candidates/", response_class=HTMLResponse)
async def candidate_management_view(request: Request, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    candidates = db.query(Candidate).filter(Candidate.user_id == user.id).order_by(desc(Candidate.created_at)).all()
    return templates.TemplateResponse(request=request, name="candidate_management.html", context={"request": request, "user": user, "title": "Candidate Management", "candidates": candidates})

@router.get("/candidates/add/", response_class=HTMLResponse)
async def candidate_add_view(request: Request, user: User = Depends(require_auth)):
    return templates.TemplateResponse(request=request, name="candidate_form.html", context={"request": request, "user": user, "title": "Add Candidate", "candidate": None})

@router.post("/candidates/add/")
async def candidate_add_post(request: Request, full_name: str = Form(...), target_role: str = Form(""), email: str = Form(""), user: User = Depends(require_auth), db: Session = Depends(get_db)):
    if not full_name.strip():
        return templates.TemplateResponse(request=request, name="candidate_form.html", context={"request": request, "user": user, "title": "Add Candidate", "error": "Full name is required."})
        
    first_name = ""
    last_name = ""
    if " " in full_name:
        first_name, last_name = full_name.split(" ", 1)
    else:
        first_name = full_name
        
    new_candidate = Candidate(
        user_id=user.id,
        full_name=full_name.strip(),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        target_role=target_role.strip(),
        email=email.strip() or None
    )
    db.add(new_candidate)
    db.commit()
    return RedirectResponse(url="/candidates/", status_code=303)

@router.get("/candidates/{candidate_id}/", response_class=HTMLResponse)
async def candidate_detail_view(request: Request, candidate_id: str, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.user_id == user.id).first()
    if not candidate:
        return RedirectResponse(url="/candidates/", status_code=303)
        
    sessions = db.query(InterviewSession).filter(InterviewSession.candidate_id == candidate.id).order_by(desc(InterviewSession.created_at)).all()
    scores = [s.report.overall_score for s in sessions if s.report]
    average_score = round(sum(scores)/len(scores), 1) if scores else 0.0
    latest_report = sessions[0].report if sessions and sessions[0].report else None
    
    return templates.TemplateResponse(request=request, name="candidate_detail.html", context={
        "request": request, 
        "user": user, 
        "title": f"{candidate.display_name} | Candidate Detail",
        "candidate": candidate,
        "sessions": sessions,
        "average_score": average_score,
        "latest_report": latest_report,
        "session_count": len(sessions)
    })

@router.post("/candidates/{candidate_id}/delete/")
async def candidate_delete_post(request: Request, candidate_id: str, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.user_id == user.id).first()
    if candidate:
        db.delete(candidate)
        db.commit()
    return RedirectResponse(url="/candidates/", status_code=303)

@router.get("/interview", response_class=HTMLResponse)
async def interview_page(request: Request, user: User = Depends(require_auth)):
    return templates.TemplateResponse(request=request, name="interview.html", context={"request": request, "user": user, "title": "AI Interview Coach | Interview Session"})

@router.get("/report/{session_id}", response_class=HTMLResponse)
async def report_page(request: Request, session_id: str, user: User = Depends(require_auth), db: Session = Depends(get_db)):
    session = db.query(InterviewSession).filter(InterviewSession.session_key == session_id, InterviewSession.user_id == user.id).first()
    if not session:
        return RedirectResponse(url="/dashboard/", status_code=303)
        
    report = session.report
    report_data = report.raw_report if report else {}
    summary_text = report.summary if report else "Interview completed successfully."
    suggestions = report.suggestions if report else []
    
    return templates.TemplateResponse(request=request, name="report.html", context={
        "request": request,
        "user": user,
        "title": "AI Interview Coach | Report",
        "session_id": session_id,
        "report": report,
        "report_data": report_data,
        "summary_text": summary_text,
        "suggestions": suggestions,
        "session": session
    })

@router.get("/favicon.ico")
async def favicon():
    file_path = Path("static/assets/logo.svg")
    if file_path.exists():
        return FileResponse(file_path, media_type="image/svg+xml")
    return Response(status_code=404)
