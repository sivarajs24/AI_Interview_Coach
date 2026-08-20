"""Channels consumer for real-time webcam emotion feedback."""

import base64
from datetime import datetime, timezone

import cv2
import numpy as np
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async

from .services import get_facial_analyzer


class EmotionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        await self.accept()
        await self.send_json({"event": "socket_ready", "success": True, "message": "Emotion stream connected."})

    async def receive_json(self, content: dict, **kwargs: object) -> None:
        frame_data = str(content.get("frame", ""))
        session_id = str(content.get("session_id", ""))
        if not frame_data:
            await self.send_json({"event": "emotion_update", "success": False, "error": "Missing frame payload."})
            return
        try:
            payload = frame_data.split(",", 1)[1] if "," in frame_data else frame_data
            frame = cv2.imdecode(np.frombuffer(base64.b64decode(payload), dtype=np.uint8), cv2.IMREAD_COLOR)
        except (ValueError, TypeError):
            frame = None
        if frame is None:
            await self.send_json({"event": "emotion_update", "success": False, "error": "Invalid frame payload."})
            return
        analysis = await sync_to_async(get_facial_analyzer().analyze_realtime_frame)(frame)
        await self.send_json({
            "event": "emotion_update",
            "success": True,
            "session_id": session_id,
            "emotion": analysis.get("dominant_emotion", "unknown"),
            "confidence": analysis.get("confidence", 0.0),
            "emotion_scores": analysis.get("emotion_scores", {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })