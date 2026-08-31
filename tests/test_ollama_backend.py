from modules.qwen_interviewer import QwenInterviewLLM


def test_ollama_backend_defaults_are_available():
    llm = QwenInterviewLLM(
        enabled=True,
        primary_model="ollama/qwen3:4b",
        fallback_model="ollama/qwen3:4b",
    )

    assert llm.backend == "ollama"
    assert llm.ollama_base_url == "http://127.0.0.1:11434"
    assert llm.ollama_model == "qwen3:4b"


def test_ollama_payload_has_prompt_and_model():
    llm = QwenInterviewLLM(
        enabled=True,
        primary_model="ollama/qwen3:4b",
        fallback_model="ollama/qwen3:4b",
        ollama_model="qwen3:4b",
    )

    payload = llm._build_ollama_payload("Reply with exactly: hello")
    assert payload["model"] == "qwen3:4b"
    assert "Reply with exactly: hello" in payload["prompt"]
