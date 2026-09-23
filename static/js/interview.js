class InterviewController {
    /**
     * Coordinate interview flow, media recording, uploads, and live UI updates.
     */
    constructor(config) {
        this.config = config;
        this.sessionId = "";
        this.questions = [];
        this.currentQuestionIndex = 0;
        this.timerHandle = null;
        this.timeLeft = config.questionDurationSeconds;
        this.recorder = null;
        this.speechRecognition = null;
        this.liveTranscript = "";
        this.isUploading = false;
        this.typeAnimationToken = 0;

        this.elements = {
            webcamFeed: document.getElementById("webcamFeed"),
            progressText: document.getElementById("progressText"),
            progressBar: document.getElementById("progressBar"),
            categoryBadge: document.getElementById("categoryBadge"),
            questionTimer: document.getElementById("questionTimer"),
            questionText: document.getElementById("questionText"),
            transcriptPreview: document.getElementById("transcriptPreview"),
            emotionBadge: document.getElementById("emotionBadge"),
            emotionText: document.getElementById("emotionText"),
            emotionConfidence: document.getElementById("emotionConfidence"),
            recordingIndicator: document.getElementById("recordingIndicator"),
            speechStatus: document.getElementById("speechStatus"),
            startBtn: document.getElementById("startSpeakingBtn"),
            nextBtn: document.getElementById("nextQuestionBtn"),
            finishBtn: document.getElementById("finishInterviewBtn"),
            interimSentiment: document.getElementById("interimSentiment"),
            interimEmotion: document.getElementById("interimEmotion"),
            interimDuration: document.getElementById("interimDuration"),
        };
    }

    /**
     * Run startup sequence for session creation and media initialization.
     */
    async init() {
        this.bindEvents();
        this.initializeSpeechRecognition();

        try {
            window.UI.setLoading(true, "Starting interview session...");
            await this.startSession();
            await this.initializeRecorder();
            this.renderCurrentQuestion();
            this.startTimer();
            window.UI.showToast("Interview ready. Start when you are comfortable.", "info");
        } catch (error) {
            window.UI.showToast(`Failed to initialize interview: ${error.message}`, "error");
        } finally {
            window.UI.setLoading(false);
        }

        window.addEventListener("beforeunload", () => {
            if (this.recorder) {
                this.recorder.destroy();
            }
        });
    }

    /**
     * Bind all primary click handlers.
     */
    bindEvents() {
        this.elements.startBtn.addEventListener("click", () => this.startSpeaking());
        this.elements.nextBtn.addEventListener("click", () => this.nextQuestion());
        this.elements.finishBtn.addEventListener("click", () => this.finishInterview());
    }

    /**
     * Helper to get CSRF token from cookies.
     */
    getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    /**
     * Request backend to create a fresh interview session.
     */
    async startSession() {
        const response = await fetch(this.config.startSessionUrl, {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "X-CSRFToken": this.getCookie("csrftoken")
            },
            body: JSON.stringify({}),
        });

        if (!response.ok) {
            throw new Error("Could not start interview session.");
        }

        const payload = await response.json();
        this.sessionId = payload.session_id;
        this.questions = payload.questions || [];

        if (!this.questions.length) {
            throw new Error("No interview questions were returned.");
        }
    }

    /**
     * Initialize media recorder and socket emotion events.
     */
    async initializeRecorder() {
        this.recorder = new window.InterviewRecorder(this.elements.webcamFeed);

        this.recorder.addEventListener("permission-error", () => {
            window.UI.showPermissionModal();
            window.UI.showToast("Camera or microphone permission denied.", "error");
        });

        this.recorder.addEventListener("emotion-update", (event) => {
            this.updateEmotionBadge(event.detail);
        });

        this.recorder.addEventListener("recording-state", (event) => {
            const { recording } = event.detail;
            this.elements.recordingIndicator.classList.toggle("hidden", !recording);
            this.elements.startBtn.disabled = recording;
            this.elements.nextBtn.disabled = !recording;
        });

        this.recorder.addEventListener("socket-error", () => {
            window.UI.showToast("Live emotion stream is temporarily unavailable.", "warning");
        });

        await this.recorder.initializeMedia();
        this.recorder.connectSocket(this.config.socketUrl);
        this.recorder.startEmotionStream(this.sessionId);
    }

    /**
     * Initialize Web Speech API for real-time transcript preview.
     */
    initializeSpeechRecognition() {
        const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!Recognition) {
            this.elements.transcriptPreview.textContent =
                "Speech preview is not supported in this browser. Final transcript will appear after processing.";
            this.elements.speechStatus.textContent = "Unavailable";
            return;
        }

        this.speechRecognition = new Recognition();
        this.speechRecognition.lang = "en-US";
        this.speechRecognition.continuous = true;
        this.speechRecognition.interimResults = true;

        this.speechRecognition.onstart = () => {
            this.elements.speechStatus.textContent = "Listening";
        };

        this.speechRecognition.onresult = (event) => {
            let interim = "";
            let finalText = "";

            for (let i = event.resultIndex; i < event.results.length; i += 1) {
                const result = event.results[i];
                const transcript = result[0].transcript;
                if (result.isFinal) {
                    finalText += `${transcript} `;
                } else {
                    interim += transcript;
                }
            }

            this.liveTranscript = `${this.liveTranscript} ${finalText}`.trim();
            const preview = `${this.liveTranscript} ${interim}`.trim();
            this.elements.transcriptPreview.textContent = preview || "Listening...";

            if (preview) {
                this.updateInterimTranscriptMetrics(preview);
            }
        };

        this.speechRecognition.onerror = (event) => {
            this.elements.speechStatus.textContent = "Issue";

            const ignoredErrors = ["no-speech", "aborted"];
            if (!ignoredErrors.includes(event.error)) {
                window.UI.showToast("Speech preview had a temporary interruption.", "warning");
            }
        };

        this.speechRecognition.onend = () => {
            if (this.recorder && this.recorder.isRecording) {
                try {
                    this.speechRecognition.start();
                } catch (_) {
                    this.elements.speechStatus.textContent = "Paused";
                }
            } else {
                this.elements.speechStatus.textContent = "Idle";
            }
        };
    }

    /**
     * Render currently active interview question with typing animation.
     */
    async renderCurrentQuestion() {
        const question = this.questions[this.currentQuestionIndex];
        const index = this.currentQuestionIndex + 1;
        const total = this.questions.length;

        this.elements.progressText.textContent = `Question ${index} of ${total}`;
        this.elements.progressBar.style.width = `${(index / total) * 100}%`;
        this.elements.categoryBadge.textContent = question.category || "General";

        this.elements.questionText.textContent = "";
        const token = this.typeAnimationToken + 1;
        this.typeAnimationToken = token;
        await this.typeText(this.elements.questionText, question.question || "", 12, token);

        this.elements.transcriptPreview.textContent = "Start speaking to see your answer preview in real time.";
        this.elements.speechStatus.textContent = "Idle";
        this.elements.interimSentiment.textContent = "-";
        this.elements.interimEmotion.textContent = "-";
        this.elements.interimDuration.textContent = "0s";
        this.elements.startBtn.disabled = false;
        this.elements.nextBtn.disabled = true;

        this.timeLeft = this.config.questionDurationSeconds;
        this.updateTimerText();
    }

    /**
     * Animate question text typing.
     * @param {HTMLElement} target
     * @param {string} text
     * @param {number} speed
     */
    async typeText(target, text, speed, token) {
        target.textContent = "";
        for (let i = 0; i < text.length; i += 1) {
            if (token !== this.typeAnimationToken) {
                return;
            }
            target.textContent += text.charAt(i);
            await new Promise((resolve) => setTimeout(resolve, speed));
        }
    }

    /**
     * Start countdown timer for the active question.
     */
    startTimer() {
        if (this.timerHandle) {
            clearInterval(this.timerHandle);
        }

        this.timerHandle = setInterval(async () => {
            this.timeLeft -= 1;
            this.updateTimerText();

            if (this.timeLeft <= 0) {
                clearInterval(this.timerHandle);
                if (this.recorder && this.recorder.isRecording && !this.isUploading) {
                    window.UI.showToast("Time is up. Submitting your answer...", "warning");
                    await this.nextQuestion();
                } else {
                    window.UI.showToast("Time is up. Please proceed to next question.", "warning");
                    this.elements.nextBtn.disabled = false;
                }
            }
        }, 1000);
    }

    /**
     * Update timer display text in mm:ss format.
     */
    updateTimerText() {
        const minutes = Math.floor(this.timeLeft / 60)
            .toString()
            .padStart(2, "0");
        const seconds = Math.max(0, this.timeLeft % 60)
            .toString()
            .padStart(2, "0");
        this.elements.questionTimer.textContent = `${minutes}:${seconds}`;

        const critical = this.timeLeft <= 20;
        this.elements.questionTimer.classList.toggle("is-critical", critical);
    }

    /**
     * Start speaking flow: begin recording and live speech preview.
     */
    startSpeaking() {
        try {
            this.liveTranscript = "";
            this.recorder.startRecording();
            if (this.speechRecognition) {
                this.speechRecognition.start();
            }
            this.elements.speechStatus.textContent = "Listening";
            this.elements.transcriptPreview.textContent = "Listening...";
            this.elements.interimSentiment.textContent = "Analyzing...";
            this.elements.interimEmotion.textContent = "Streaming...";
            window.UI.showToast("Recording started.", "success");
        } catch (error) {
            window.UI.showToast(`Unable to start recording: ${error.message}`, "error");
        }
    }

    /**
     * Stop recording, upload response, and move to the next question.
     */
    async nextQuestion() {
        if (!this.recorder || !this.recorder.isRecording || this.isUploading) {
            return;
        }

        this.isUploading = true;
        window.UI.setLoading(true, "Analyzing response...");

        try {
            if (this.speechRecognition) {
                this.speechRecognition.stop();
            }

            const { audioBlob, videoBlob, durationSeconds } = await this.recorder.stopRecording();
            if (durationSeconds < 3) {
                this.elements.interimDuration.textContent = `${durationSeconds.toFixed(1)}s`;
                window.UI.showToast("Answer too short. Please speak for at least 3 seconds.", "warning");
                this.elements.startBtn.disabled = false;
                this.elements.nextBtn.disabled = true;
                return;
            }

            const result = await this.uploadResponse({
                audioBlob,
                videoBlob,
                durationSeconds,
            });

            const sentimentLabel = result.sentiment?.label || "unknown";
            const sentimentConfidence = Number(result.sentiment?.confidence || 0);

            this.elements.interimSentiment.textContent = `${sentimentLabel} (${Math.round(sentimentConfidence)}%)`;
            this.elements.interimEmotion.textContent = result.dominant_emotion || "unknown";
            this.elements.interimDuration.textContent = `${durationSeconds.toFixed(1)}s`;
            this.elements.transcriptPreview.textContent = result.transcript || "Transcription unavailable";

            this.currentQuestionIndex += 1;
            if (this.currentQuestionIndex >= this.questions.length) {
                clearInterval(this.timerHandle);
                this.elements.nextBtn.classList.add("hidden");
                this.elements.finishBtn.classList.remove("hidden");
                this.elements.finishBtn.disabled = false;
                this.elements.startBtn.disabled = true;
                this.elements.questionText.textContent = "All questions completed. Review your final report now.";
                this.elements.progressText.textContent = `Question ${this.questions.length} of ${this.questions.length}`;
                this.elements.progressBar.style.width = "100%";
                this.elements.categoryBadge.textContent = "Completed";
                window.UI.showToast("Interview questions completed.", "success");
                return;
            }

            await this.renderCurrentQuestion();
            this.startTimer();
        } catch (error) {
            window.UI.showToast(`Upload failed: ${error.message}`, "error");
            this.elements.startBtn.disabled = false;
            this.elements.nextBtn.disabled = true;
        } finally {
            this.isUploading = false;
            window.UI.setLoading(false);
        }
    }

    /**
     * Upload one question response payload to backend.
     * @param {{audioBlob: Blob, videoBlob: Blob, durationSeconds: number}} payload
     * @returns {Promise<any>}
     */
    async uploadResponse(payload) {
        const formData = new FormData();
        formData.append("session_id", this.sessionId);
        formData.append("question_index", String(this.currentQuestionIndex));
        formData.append("duration_seconds", String(payload.durationSeconds));
        formData.append("audio", payload.audioBlob, `audio-q${this.currentQuestionIndex}.webm`);
        formData.append("video", payload.videoBlob, `video-q${this.currentQuestionIndex}.webm`);

        const response = await fetch(this.config.uploadResponseUrl, {
            method: "POST",
            headers: {
                "X-CSRFToken": this.getCookie("csrftoken")
            },
            body: formData,
        });

        const result = await response.json();
        if (!response.ok || !result.success) {
            const message = result.error?.message || "Could not process this response.";
            throw new Error(message);
        }

        // Poll for completion if status is processing
        if (result.status === "processing") {
            return await this.pollResponseStatus(this.currentQuestionIndex);
        }

        return result;
    }

    /**
     * Poll the backend until the response processing is completed.
     */
    async pollResponseStatus(questionIndex) {
        const url = `/api/response_status/${this.sessionId}/${questionIndex}/`;
        
        while (true) {
            await new Promise((resolve) => setTimeout(resolve, 2000)); // Poll every 2 seconds
            
            const response = await fetch(url, {
                method: "GET",
                headers: {
                    "X-CSRFToken": this.getCookie("csrftoken")
                }
            });
            
            if (!response.ok) {
                throw new Error("Failed to check response status.");
            }
            
            const result = await response.json();
            if (result.status === "completed") {
                return result;
            } else if (result.status === "error") {
                throw new Error("An error occurred during response processing.");
            }
            // If still processing, loop again
        }
    }

    /**
     * Finalize the interview and navigate to report page.
     */
    async finishInterview() {
        try {
            window.UI.setLoading(true, "Generating final report...");
            const response = await fetch(this.config.finishInterviewUrl, {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    "X-CSRFToken": this.getCookie("csrftoken")
                },
                body: JSON.stringify({ session_id: this.sessionId }),
            });

            const payload = await response.json();
            if (!response.ok || !payload.success) {
                throw new Error(payload.error?.message || "Failed to finish interview.");
            }

            const reportUrl = this.config.reportPagePrefix.replace("__SESSION__", this.sessionId);
            window.location.href = reportUrl;
        } catch (error) {
            window.UI.showToast(error.message, "error");
        } finally {
            window.UI.setLoading(false);
        }
    }

    /**
     * Update emotion badge from backend live stream payload.
     * @param {any} payload
     */
    updateEmotionBadge(payload) {
        if (!payload || !payload.success) {
            return;
        }

        const emotion = String(payload.emotion || "unknown").toLowerCase();
        const confidence = Number(payload.confidence || 0);

        this.elements.emotionBadge.textContent = emotion;
        this.elements.emotionBadge.className = `emotion-badge emotion-${emotion}`;
        this.elements.emotionText.textContent = emotion;
        this.elements.emotionConfidence.textContent = `${Math.round(confidence)}%`;

        if (emotion === "unknown") {
            this.elements.interimEmotion.textContent = "Detecting...";
        }
    }

    /**
     * Estimate short transcript metrics for live coaching context.
     * @param {string} transcript
     */
    updateInterimTranscriptMetrics(transcript) {
        const words = transcript
            .split(/\s+/)
            .map((item) => item.trim())
            .filter(Boolean);

        if (!words.length) {
            return;
        }

        const fillers = ["um", "uh", "like", "you know", "basically", "actually", "sort of"];
        const lowerTranscript = transcript.toLowerCase();
        let fillerCount = 0;

        fillers.forEach((token) => {
            if (token.includes(" ")) {
                if (lowerTranscript.includes(token)) {
                    fillerCount += 1;
                }
                return;
            }

            words.forEach((word) => {
                if (word.toLowerCase().replace(/[^a-z]/g, "") === token) {
                    fillerCount += 1;
                }
            });
        });

        const fillerRatio = fillerCount / words.length;
        if (fillerRatio > 0.08) {
            this.elements.interimSentiment.textContent = "Reduce filler words";
        } else if (words.length >= 14) {
            this.elements.interimSentiment.textContent = "Good detail level";
        }
    }
}

window.addEventListener("DOMContentLoaded", () => {
    const controller = new InterviewController(window.INTERVIEW_CONFIG);
    controller.init();
});
