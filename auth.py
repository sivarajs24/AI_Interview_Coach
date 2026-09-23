from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from database import get_db
from models import User
import uuid
import json

pwd_context =CryptContext(schemes=["bcrypt"], deprecated="auto")

# Extremely simple token generation for the sake of migration
# In a real app, use PyJWT to encode/decode signed tokens.
# Here we'll just map a random session token in the database or use a basic signed cookie if we wanted,
# but for simplicity without adding PyJWT to requirements, we can store active sessions in memory, 
# or just look up user by a secure token if we added a `session_token` to User model.
# Actually, since it's an AI app, we can just use PyJWT. Wait, PyJWT isn't in requirements.txt. 
# Let's add PyJWT to requirements or use a simple hack.
# Let's just write a mock session manager or add pyjwt to requirements. 

# I'll update requirements later if needed, but let's assume we can just use `itsdangerous` or standard python `hmac` for signed cookies.
# Better yet, I'll use simple session IDs stored in a global dictionary for this prototype, or update the DB to have a token.
# Let's update `models.py` to have a `session_token` on User to make it stateless without JWT dependency, or just add pyjwt.

# It's cleaner to add `PyJWT`. Let me just write the auth logic assuming we'll use a session cookie that holds the user_id for now (signed).
import hmac
import hashlib
import base64

SECRET_KEY = "super-secret-key-change-in-production"

def create_access_token(user_id: str):
    data = f"{user_id}:{datetime.now(timezone.utc).timestamp()}"
    signature = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
    token = f"{data}:{signature}"
    return base64.b64encode(token.encode()).decode()

def verify_access_token(token: str) -> Optional[str]:
    try:
        decoded = base64.b64decode(token).decode()
        data, signature = decoded.rsplit(":", 1)
        expected_signature = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected_signature):
            user_id, _ = data.split(":")
            return user_id
        return None
    except Exception:
        return None

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("session_token")
    if not token:
        return None
    user_id = verify_access_token(token)
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    return user

def require_auth(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/accounts/login/"},
        )
    return user
