from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
import os

# Create database tables
Base.metadata.create_all(bind=engine)

from routers import web, api
# Import websocket logic if it's extracted, or we can just define it here.
# For now, let's copy the websocket logic from ai_service/main.py to routers/ws.py or here.
from fastapi import WebSocket, WebSocketDisconnect
import json
import cv2
import numpy as np
import base64
from datetime import datetime, timezone
from modules.facial_analyzer import FacialAnalyzer

app = FastAPI(title="AI Interview Coach")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(web.router)
app.include_router(api.router, prefix="/api")

# Load facial analyzer once
try:
    facial_analyzer = FacialAnalyzer()
except Exception as e:
    print(f"Warning: Failed to load FacialAnalyzer: {e}")
    facial_analyzer = None

@app.websocket("/ws/emotion")
async def websocket_emotion(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"event": "socket_ready", "success": True, "message": "Emotion stream connected."})
    
    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                content = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            frame_data = str(content.get("frame", ""))
            session_id = str(content.get("session_id", ""))
            
            if not frame_data:
                await websocket.send_json({"event": "emotion_update", "success": False, "error": "Missing frame payload."})
                continue
                
            try:
                payload = frame_data.split(",", 1)[1] if "," in frame_data else frame_data
                frame = cv2.imdecode(np.frombuffer(base64.b64decode(payload), dtype=np.uint8), cv2.IMREAD_COLOR)
            except (ValueError, TypeError):
                frame = None
                
            if frame is None:
                await websocket.send_json({"event": "emotion_update", "success": False, "error": "Invalid frame payload."})
                continue
                
            if facial_analyzer:
                analysis = facial_analyzer.analyze_realtime_frame(frame)
            else:
                analysis = {"dominant_emotion": "unknown", "confidence": 0.0, "emotion_scores": {}}
            
            await websocket.send_json({
                "event": "emotion_update",
                "success": True,
                "session_id": session_id,
                "emotion": analysis.get("dominant_emotion", "unknown"),
                "confidence": analysis.get("confidence", 0.0),
                "emotion_scores": analysis.get("emotion_scores", {}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    except WebSocketDisconnect:
        pass
