"""Application services shared by Django HTTP views and websocket consumers."""

from functools import lru_cache


@lru_cache(maxsize=1)
def get_transcriber():
	from modules.whisper_transcriber import WhisperTranscriber

	return WhisperTranscriber(model_name="base")


@lru_cache(maxsize=1)
def get_sentiment_analyzer():
	from modules.sentiment_analyzer import SentimentAnalyzer

	return SentimentAnalyzer(model_name="cardiffnlp/twitter-roberta-base-sentiment-latest")


@lru_cache(maxsize=1)
def get_facial_analyzer():
	from modules.facial_analyzer import FacialAnalyzer

	return FacialAnalyzer(frame_step=30)


@lru_cache(maxsize=1)
def get_interview_engine():
	from modules.interview_engine import InterviewEngine
	from modules.qwen_interviewer import QwenInterviewLLM

	return InterviewEngine(llm_client=QwenInterviewLLM())


@lru_cache(maxsize=1)
def get_report_generator():
	from modules.report_generator import ReportGenerator

	return ReportGenerator()