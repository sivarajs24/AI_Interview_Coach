from modules.qwen_interviewer import QwenInterviewLLM


def test_cloud_provider_defaults_to_free_gemini_tier():
    llm = QwenInterviewLLM(
        enabled=True,
        primary_model="Qwen/Qwen2.5-3B-Instruct",
        fallback_model="Qwen/Qwen2.5-1.5B-Instruct",
        cloud_provider="gemini",
        cloud_api_key="test-key",
        cloud_model="gemini-2.0-flash",
    )

    assert llm.cloud_provider == "gemini"
    assert llm.cloud_model == "gemini-2.0-flash"
    assert llm.cloud_api_key == "test-key"
    assert llm.cloud_providers[0] == "gemini"
    assert llm.cloud_providers[1] == "groq"


def test_cloud_fallback_uses_openai_compatible_payload_for_groq():
    llm = QwenInterviewLLM(
        enabled=True,
        primary_model="Qwen/Qwen2.5-3B-Instruct",
        fallback_model="Qwen/Qwen2.5-1.5B-Instruct",
        cloud_provider="groq",
        cloud_api_key="test-key",
        cloud_model="llama-3.1-8b-instant",
    )

    payload = llm._build_cloud_payload("hello", "gemini-2.0-flash")
    assert payload["model"] == "gemini-2.0-flash"
    assert payload["messages"][0]["role"] == "user"
    assert "hello" in payload["messages"][0]["content"]


def test_cloud_provider_order_prefers_gemini_then_groq():
    llm = QwenInterviewLLM(cloud_provider="gemini")
    assert llm.cloud_providers[:2] == ["gemini", "groq"]
