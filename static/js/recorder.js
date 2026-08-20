class InterviewRecorder extends EventTarget {
    /**
     * Manage browser media capture for webcam, microphone, recording, and emotion streaming.
     * @param {HTMLVideoElement} videoElement
     */
    constructor(videoElement) {
        super();
        this.videoElement = videoElement;
        this.stream = null;
        this.audioRecorder = null;
        this.videoRecorder = null;
        this.audioChunks = [];
        this.videoChunks = [];
        this.recordingStartTime = 0;
        this.isRecording = false;
        this.socket = null;
        this.emotionInterval = null;
        this.sessionId = null;
    }

    /**
     * Initialize webcam and microphone stream.
     * @returns {Promise<void>}
     */
    async initializeMedia() {
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                    facingMode: "user",
                },
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    channelCount: 1,
                },
            });

            this.videoElement.srcObject = this.stream;
            await this.videoElement.play();
        } catch (error) {
            this.dispatchEvent(
                new CustomEvent("permission-error", {
                    detail: { error },
                })
            );
            throw error;
        }
    }

    /**
    * Connect to the Django Channels websocket for live emotion updates.
    * @param {string} socketUrl
     */
    connectSocket(socketUrl) {
        this.socket = new WebSocket(socketUrl);

        this.socket.addEventListener("message", (event) => {
            const payload = JSON.parse(event.data);
            if (payload.event === "socket_ready") {
                this.dispatchEvent(new CustomEvent("socket-ready", { detail: payload }));
            }
            if (payload.event === "emotion_update") {
                this.dispatchEvent(new CustomEvent("emotion-update", { detail: payload }));
            }
        });

        this.socket.addEventListener("error", (error) => {
            this.dispatchEvent(new CustomEvent("socket-error", { detail: { error } }));
        });
    }

    /**
     * Begin frame streaming every 2 seconds for real-time emotion analysis.
     * @param {string} sessionId
     */
    startEmotionStream(sessionId) {
        if (!this.socket || !this.stream) {
            return;
        }

        this.sessionId = sessionId;
        this.stopEmotionStream();

        this.emotionInterval = setInterval(() => {
            const frame = this.captureFrameAsDataUrl();
            if (!frame) {
                return;
            }
            if (this.socket.readyState !== WebSocket.OPEN) {
                return;
            }
            this.socket.send(JSON.stringify({
                session_id: this.sessionId,
                frame,
            }));
        }, 2000);
    }

    /**
     * Stop periodic frame streaming.
     */
    stopEmotionStream() {
        if (this.emotionInterval) {
            clearInterval(this.emotionInterval);
            this.emotionInterval = null;
        }
    }

    /**
     * Capture the current webcam frame as JPEG data URL.
     * @returns {string|null}
     */
    captureFrameAsDataUrl() {
        if (!this.videoElement.videoWidth || !this.videoElement.videoHeight) {
            return null;
        }

        const canvas = document.createElement("canvas");
        canvas.width = this.videoElement.videoWidth;
        canvas.height = this.videoElement.videoHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
            return null;
        }

        ctx.drawImage(this.videoElement, 0, 0, canvas.width, canvas.height);
        return canvas.toDataURL("image/jpeg", 0.75);
    }

    /**
     * Start recording both audio-only and webcam video streams.
     */
    startRecording() {
        if (!this.stream) {
            throw new Error("Media stream is not initialized.");
        }
        if (this.isRecording) {
            return;
        }

        this.audioChunks = [];
        this.videoChunks = [];

        const audioStream = new MediaStream(this.stream.getAudioTracks());
        const audioMimeType = this.resolveMimeType([
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/mp4",
        ]);
        const videoMimeType = this.resolveMimeType([
            "video/webm;codecs=vp9,opus",
            "video/webm;codecs=vp8,opus",
            "video/webm",
        ]);

        this.audioRecorder = new MediaRecorder(audioStream, audioMimeType ? { mimeType: audioMimeType } : undefined);
        this.videoRecorder = new MediaRecorder(this.stream, videoMimeType ? { mimeType: videoMimeType } : undefined);

        this.audioRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                this.audioChunks.push(event.data);
            }
        };

        this.videoRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                this.videoChunks.push(event.data);
            }
        };

        this.audioRecorder.start(1000);
        this.videoRecorder.start(1000);
        this.recordingStartTime = Date.now();
        this.isRecording = true;

        this.dispatchEvent(new CustomEvent("recording-state", { detail: { recording: true } }));
    }

    /**
     * Stop active recordings and resolve with audio/video blobs plus duration.
     * @returns {Promise<{audioBlob: Blob, videoBlob: Blob, durationSeconds: number}>}
     */
    async stopRecording() {
        if (!this.isRecording || !this.audioRecorder || !this.videoRecorder) {
            throw new Error("Recording is not active.");
        }

        const audioMimeType = this.audioRecorder.mimeType || "audio/webm";
        const videoMimeType = this.videoRecorder.mimeType || "video/webm";

        const audioPromise = this.stopRecorder(this.audioRecorder, this.audioChunks, audioMimeType);
        const videoPromise = this.stopRecorder(this.videoRecorder, this.videoChunks, videoMimeType);

        const [audioBlob, videoBlob] = await Promise.all([audioPromise, videoPromise]);
        const durationSeconds = Math.max(0, (Date.now() - this.recordingStartTime) / 1000);

        this.isRecording = false;
        this.dispatchEvent(new CustomEvent("recording-state", { detail: { recording: false } }));

        return {
            audioBlob,
            videoBlob,
            durationSeconds,
        };
    }

    /**
     * Stop one MediaRecorder and resolve a Blob from recorded chunks.
     * @param {MediaRecorder} recorder
     * @param {BlobPart[]} chunks
     * @param {string} mimeType
     * @returns {Promise<Blob>}
     */
    stopRecorder(recorder, chunks, mimeType) {
        return new Promise((resolve) => {
            const onStop = () => {
                recorder.removeEventListener("stop", onStop);
                resolve(new Blob(chunks, { type: mimeType }));
            };

            recorder.addEventListener("stop", onStop);
            if (recorder.state !== "inactive") {
                recorder.stop();
            } else {
                onStop();
            }
        });
    }

    /**
     * Select the first browser-supported mime type from candidates.
     * @param {string[]} candidates
     * @returns {string|null}
     */
    resolveMimeType(candidates) {
        for (const candidate of candidates) {
            if (MediaRecorder.isTypeSupported(candidate)) {
                return candidate;
            }
        }
        return null;
    }

    /**
     * Release media and socket resources.
     */
    destroy() {
        this.stopEmotionStream();
        if (this.stream) {
            this.stream.getTracks().forEach((track) => track.stop());
            this.stream = null;
        }
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }
    }
}

window.InterviewRecorder = InterviewRecorder;
