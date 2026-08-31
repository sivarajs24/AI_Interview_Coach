"""Core analysis and orchestration modules for InterviewIQ."""

from importlib import import_module

__all__ = [
    "WhisperTranscriber",
    "SentimentAnalyzer",
    "FacialAnalyzer",
    "InterviewEngine",
    "QwenInterviewLLM",
    "ReportGenerator",
]


def __getattr__(name):
    """Lazily import optional modules so package import does not require all heavy runtime dependencies."""
    module_map = {
        "WhisperTranscriber": ".whisper_transcriber",
        "SentimentAnalyzer": ".sentiment_analyzer",
        "FacialAnalyzer": ".facial_analyzer",
        "InterviewEngine": ".interview_engine",
        "QwenInterviewLLM": ".qwen_interviewer",
        "ReportGenerator": ".report_generator",
    }

    if name not in module_map:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_map[name], __name__)
    return getattr(module, name)
