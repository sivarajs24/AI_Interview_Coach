"""Local Qwen LLM orchestrator with automatic fallback for constrained hardware."""

from __future__ import annotations

import gc
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


class QwenInterviewLLM:
    """Generate role-specific interview prompts using local Qwen models with fallback control."""

    _TRUTHY = {"1", "true", "yes", "on"}

    def __init__(
        self,
        enabled: Optional[bool] = None,
        primary_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        low_vram_mb: Optional[int] = None,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        cloud_provider: Optional[str] = None,
        cloud_providers: Optional[List[str]] = None,
        cloud_api_key: Optional[str] = None,
        cloud_model: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        backend: Optional[str] = None,
    ) -> None:
        """Initialize lazy-loading Qwen runtime settings from parameters or environment variables."""
        self.enabled = enabled if enabled is not None else self._env_bool("QWEN_ENABLED", True)
        self.primary_model = primary_model or os.getenv("QWEN_PRIMARY_MODEL", "Qwen/Qwen2.5-3B-Instruct")
        self.fallback_model = fallback_model or os.getenv(
            "QWEN_FALLBACK_MODEL", "Qwen/Qwen2.5-1.5B-Instruct"
        )
        self.timeout_seconds = (
            max(2.0, timeout_seconds)
            if timeout_seconds is not None
            else max(2.0, self._env_float("QWEN_TIMEOUT_SECONDS", 60.0))
        )
        self.low_vram_mb = (
            max(256, low_vram_mb)
            if low_vram_mb is not None
            else max(256, self._env_int("QWEN_GPU_LOW_VRAM_MB", 1600))
        )
        self.max_new_tokens = (
            max(64, max_new_tokens)
            if max_new_tokens is not None
            else max(64, self._env_int("QWEN_MAX_NEW_TOKENS", 220))
        )
        self.temperature = (
            min(1.2, max(0.1, temperature))
            if temperature is not None
            else min(1.2, max(0.1, self._env_float("QWEN_TEMPERATURE", 0.7)))
        )

        self.ollama_base_url = (ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        parsed_model = self._normalize_model_name(ollama_model or os.getenv("OLLAMA_MODEL", "qwen3:4b"))
        self.ollama_model = parsed_model
        self.backend = (backend or os.getenv("LLM_BACKEND", "ollama")).lower()
        if self.primary_model.startswith("ollama/"):
            self.backend = "ollama"
        if self.fallback_model.startswith("ollama/"):
            self.backend = "ollama"
        if self.backend == "ollama":
            self.timeout_seconds = max(self.timeout_seconds, self._env_float("OLLAMA_TIMEOUT_SECONDS", 60.0))

        self.cloud_provider = (cloud_provider or os.getenv("CLOUD_LLM_PROVIDER", "gemini") or "gemini").lower()
        self.cloud_providers = self._resolve_cloud_provider_order(cloud_providers)
        self.cloud_provider = self.cloud_providers[0]
        self.cloud_api_key = cloud_api_key or self._resolve_cloud_api_key(self.cloud_provider)
        self.cloud_model = cloud_model or os.getenv("CLOUD_LLM_MODEL", self._default_cloud_model(self.cloud_provider))

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._tokenizer: Optional[Any] = None
        self._model: Optional[Any] = None
        self._active_model_name: Optional[str] = None
        self._lock = threading.RLock()

    def _resolve_cloud_provider_order(self, explicit_providers: Optional[List[str]]) -> List[str]:
        """Return the provider fallback order, preferring Gemini first and Groq second."""
        env_value = os.getenv("CLOUD_LLM_PROVIDERS")
        provider_names = explicit_providers or (
            [part.strip().lower() for part in env_value.split(",") if part.strip()] if env_value else None
        )

        ordered = ["gemini", "groq", "openai", "openrouter"]
        if provider_names:
            selected = []
            for provider in provider_names:
                normalized = provider.strip().lower()
                if normalized and normalized not in selected:
                    selected.append(normalized)
            for provider in ordered:
                if provider not in selected:
                    selected.append(provider)
            return selected

        default_provider = (self.cloud_provider or os.getenv("CLOUD_LLM_PROVIDER", "gemini") or "gemini").lower()
        ordered = [default_provider] + [provider for provider in ["gemini", "groq", "openai", "openrouter"] if provider != default_provider]
        return ordered

    def _normalize_model_name(self, model_name: str) -> str:
        """Normalize both raw and prefixed Ollama names into the model name expected by Ollama."""
        if model_name.startswith("ollama/"):
            return model_name.split("/", 1)[1]
        return model_name.strip()

    def _resolve_cloud_api_key(self, provider: str) -> Optional[str]:
        """Resolve the API key for the selected cloud provider from the environment."""
        key_env = {
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }.get(provider, "CLOUD_LLM_API_KEY")
        return os.getenv(key_env) or os.getenv("CLOUD_LLM_API_KEY")

    def _default_cloud_model(self, provider: str) -> str:
        """Return a sensible free-tier default model for the chosen provider."""
        defaults = {
            "gemini": "gemini-2.0-flash",
            "groq": "llama-3.1-8b-instant",
            "openai": "gpt-4o-mini",
            "openrouter": "meta-llama/llama-3.1-8b-instruct",
        }
        return defaults.get(provider, "gemini-2.0-flash")

    def _provider_api_key(self, provider: str) -> Optional[str]:
        """Get the configured API key for a specific provider."""
        key_env = {
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }.get(provider, "CLOUD_LLM_API_KEY")
        return os.getenv(key_env) or os.getenv("CLOUD_LLM_API_KEY")

    def _provider_model(self, provider: str) -> str:
        """Return the configured model name for a provider."""
        provider_model = {
            "gemini": os.getenv("GEMINI_LLM_MODEL") or os.getenv("CLOUD_LLM_MODEL", "gemini-2.0-flash"),
            "groq": os.getenv("GROQ_LLM_MODEL") or os.getenv("CLOUD_LLM_MODEL", "llama-3.1-8b-instant"),
            "openai": os.getenv("OPENAI_LLM_MODEL") or os.getenv("CLOUD_LLM_MODEL", "gpt-4o-mini"),
            "openrouter": os.getenv("OPENROUTER_LLM_MODEL") or os.getenv("CLOUD_LLM_MODEL", "meta-llama/llama-3.1-8b-instruct"),
        }
        return provider_model.get(provider, os.getenv("CLOUD_LLM_MODEL", self._default_cloud_model(provider)))

    def _env_bool(self, key: str, default: bool) -> bool:
        """Read a boolean-like environment variable with safe defaults."""
        raw = os.getenv(key)
        if raw is None:
            return default
        return raw.strip().lower() in self._TRUTHY

    def _env_float(self, key: str, default: float) -> float:
        """Read a floating-point environment variable with fallback value."""
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def _env_int(self, key: str, default: int) -> int:
        """Read an integer environment variable with fallback value."""
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _is_oom_error(self, error: Exception) -> bool:
        """Identify CUDA or memory-pressure errors from exception text."""
        msg = str(error).lower()
        markers = [
            "out of memory",
            "cuda error",
            "cublas_status_alloc_failed",
            "not enough memory",
        ]
        return any(marker in msg for marker in markers)

    def _has_memory_pressure(self) -> bool:
        """Return whether GPU free memory is below the configured low-memory threshold."""
        if self.device != "cuda":
            return False

        try:
            free_bytes, _ = torch.cuda.mem_get_info()
            free_mb = free_bytes / (1024 * 1024)
            return free_mb < self.low_vram_mb
        except Exception:
            return False

    def _cleanup_model(self) -> None:
        """Release current model and tokenizer resources before loading another model."""
        self._model = None
        self._tokenizer = None
        self._active_model_name = None

        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()

    def _build_ollama_payload(self, prompt: str) -> Dict[str, Any]:
        """Build the request payload for a local Ollama model."""
        return {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_new_tokens,
            },
        }

    def _call_ollama(self, prompt: str) -> str:
        """Query the local Ollama server and return the generated text."""
        url = f"{self.ollama_base_url}/api/generate"
        payload = self._build_ollama_payload(prompt)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
            return str(data.get("response", "")).strip()
        except Exception:
            return ""

    def _load_model(self, model_name: str) -> None:
        """Load a Qwen model and tokenizer into the current runtime device, or use Ollama if configured."""
        if self.backend == "ollama" or model_name.startswith("ollama/"):
            self._model = object()
            self._tokenizer = object()
            self._active_model_name = self.ollama_model
            return

        self._cleanup_model()

        tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=dtype,
            trust_remote_code=True,
        )

        model.to(self.device)
        model.eval()

        if tokenizer.pad_token_id is None and tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token

        self._tokenizer = tokenizer
        self._model = model
        self._active_model_name = model_name

    def _switch_to_fallback(self, reason: str) -> bool:
        """Attempt to switch to the fallback model and return whether loading succeeded."""
        _ = reason
        if not self.enabled:
            return False

        if self._active_model_name == self.fallback_model and self._model is not None:
            return True

        try:
            self._load_model(self.fallback_model)
            return True
        except Exception:
            return False

    def _ensure_model(self) -> bool:
        """Ensure a model is loaded, preferring 3B unless memory pressure suggests fallback."""
        if not self.enabled:
            return False

        if self._model is not None and self._tokenizer is not None:
            if self._active_model_name == self.primary_model and self._has_memory_pressure():
                self._switch_to_fallback("memory pressure")
            return self._model is not None and self._tokenizer is not None

        preferred = self.fallback_model if self._has_memory_pressure() else self.primary_model

        try:
            self._load_model(preferred)
            return True
        except Exception as error:
            if preferred != self.fallback_model and self._is_oom_error(error):
                return self._switch_to_fallback("oom on primary load")
            if preferred != self.fallback_model:
                return self._switch_to_fallback("primary load failure")
            return False

    def _build_rewrite_prompt(
        self,
        base_questions: List[Dict[str, Any]],
        candidate_name: str,
        target_role: str,
    ) -> str:
        """Build a strict prompt that rewrites exactly 10 questions for the target role."""
        numbered_questions = "\n".join(
            f"{idx}. [{item['category']}] {item['question']}" for idx, item in enumerate(base_questions, start=1)
        )

        return (
            "You are an expert interview coach.\n"
            f"Candidate name: {candidate_name}\n"
            f"Target role: {target_role}\n"
            "Rewrite each interview question to be specific to this role while keeping the same intent and difficulty.\n"
            "Rules:\n"
            "- Return exactly 10 lines.\n"
            "- Keep one question per line.\n"
            "- Do not include categories or explanations.\n"
            "- Do not include markdown or JSON.\n"
            "- Keep each line under 28 words.\n"
            "Output format:\n"
            "1. rewritten question\n"
            "2. rewritten question\n"
            "...\n"
            "10. rewritten question\n\n"
            "Questions to rewrite:\n"
            f"{numbered_questions}\n"
        )

    def _parse_numbered_questions(self, output_text: str, expected_count: int) -> List[str]:
        """Parse model output into a clean list of rewritten questions."""
        parsed: List[str] = []
        for raw_line in output_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            line = re.sub(r"^\d+[\)\.:-]\s*", "", line)
            line = re.sub(r"^\[[^\]]+\]\s*", "", line)
            line = line.strip().strip('"')
            if not line:
                continue

            parsed.append(line)
            if len(parsed) >= expected_count:
                break

        return parsed

    def _build_cloud_payload(self, prompt: str, model_name: str) -> Dict[str, Any]:
        """Build the request body for the selected cloud LLM provider."""
        if self.cloud_provider == "gemini":
            return {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": self.temperature,
                    "maxOutputTokens": self.max_new_tokens,
                },
                "model": model_name,
            }

        return {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_new_tokens,
        }

    def _parse_cloud_response(self, provider: str, payload: Dict[str, Any]) -> str:
        """Extract text content from a cloud provider response payload."""
        if provider == "gemini":
            candidates = payload.get("candidates") or []
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts") or []
            if not parts:
                return ""
            return "".join(part.get("text", "") for part in parts if isinstance(part, dict))

        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content)

    def _generate_cloud_once(self, prompt: str, provider: Optional[str] = None) -> str:
        """Call a cloud LLM API as a fallback when local Qwen fails."""
        provider_name = (provider or self.cloud_provider).lower()
        api_key = self._provider_api_key(provider_name)
        if not api_key:
            return ""

        model_name = self._provider_model(provider_name)
        url = None
        headers = {"Content-Type": "application/json"}

        if provider_name == "gemini":
            params = urllib.parse.urlencode({"key": api_key})
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?{params}"
            body = self._build_cloud_payload(prompt, model_name)
        elif provider_name == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            body = self._build_cloud_payload(prompt, model_name)
        elif provider_name == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            body = self._build_cloud_payload(prompt, model_name)
        elif provider_name == "openrouter":
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            headers["HTTP-Referer"] = "https://localhost"
            headers["X-Title"] = "AI Interview Coach"
            body = self._build_cloud_payload(prompt, model_name)
        else:
            return ""

        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
                result = self._parse_cloud_response(provider_name, payload)
                return result.strip()
        except Exception:
            return ""

    def _generate_once(self, prompt: str, retry_with_fallback: bool = False) -> str:
        """Run one generation call and fallback to the smaller model on timeout-like or OOM behavior."""
        if self.backend == "ollama":
            return self._call_ollama(prompt)

        if self._model is None or self._tokenizer is None:
            return ""

        try:
            tokenized = self._tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=2048,
            )
            tokenized = {key: tensor.to(self.device) for key, tensor in tokenized.items()}

            eos_id = self._tokenizer.eos_token_id
            pad_id = self._tokenizer.pad_token_id if self._tokenizer.pad_token_id is not None else eos_id

            started = time.perf_counter()
            with torch.no_grad():
                generated = self._model.generate(
                    **tokenized,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=True,
                    temperature=self.temperature,
                    top_p=0.9,
                    repetition_penalty=1.05,
                    max_time=self.timeout_seconds,
                    eos_token_id=eos_id,
                    pad_token_id=pad_id,
                )
            elapsed = time.perf_counter() - started

            prompt_tokens = tokenized["input_ids"].shape[-1]
            generated_tokens = generated[0][prompt_tokens:]
            output_text = self._tokenizer.decode(generated_tokens, skip_special_tokens=True).strip()

            if (
                self._active_model_name == self.primary_model
                and elapsed >= (self.timeout_seconds * 0.95)
                and not retry_with_fallback
            ):
                if self._switch_to_fallback("timeout on primary"):
                    return self._generate_once(prompt, retry_with_fallback=True)

            return output_text
        except RuntimeError as error:
            if self._is_oom_error(error) and not retry_with_fallback and self._switch_to_fallback("oom"):
                return self._generate_once(prompt, retry_with_fallback=True)
            return ""
        except Exception:
            return ""

    def _cloud_rewrite_questions(self, prompt: str, expected_count: int) -> Optional[List[str]]:
        """Attempt to rewrite questions across the configured cloud provider order."""
        for provider_name in self.cloud_providers:
            api_key = self._provider_api_key(provider_name)
            if not api_key:
                continue

            generated_text = self._generate_cloud_once(prompt, provider=provider_name)
            if not generated_text:
                continue

            rewritten = self._parse_numbered_questions(generated_text, expected_count)
            if len(rewritten) != expected_count:
                continue

            self.cloud_provider = provider_name
            self.cloud_api_key = api_key
            self.cloud_model = self._provider_model(provider_name)
            self._active_model_name = f"{self.cloud_provider}:{self.cloud_model}"
            return rewritten

        return None

    def rewrite_questions(
        self,
        base_questions: List[Dict[str, Any]],
        candidate_name: str,
        target_role: str,
    ) -> List[Dict[str, Any]]:
        """Rewrite interview questions with Qwen while preserving category/index layout."""
        if not base_questions:
            return base_questions

        with self._lock:
            prompt = self._build_rewrite_prompt(base_questions, candidate_name, target_role)
            if not self._ensure_model():
                cloud_rewritten = self._cloud_rewrite_questions(prompt, len(base_questions))
                if cloud_rewritten is None:
                    return base_questions
                rewritten = cloud_rewritten
            else:
                generated_text = self._generate_once(prompt)
                rewritten = self._parse_numbered_questions(generated_text, len(base_questions))

                if len(rewritten) != len(base_questions):
                    if self._active_model_name == self.primary_model and self._switch_to_fallback(
                        "invalid output on primary"
                    ):
                        generated_text = self._generate_once(prompt, retry_with_fallback=True)
                        rewritten = self._parse_numbered_questions(generated_text, len(base_questions))

                if len(rewritten) != len(base_questions):
                    cloud_rewritten = self._cloud_rewrite_questions(prompt, len(base_questions))
                    if cloud_rewritten is None:
                        return base_questions
                    rewritten = cloud_rewritten

            upgraded: List[Dict[str, Any]] = []
            for idx, base_item in enumerate(base_questions):
                upgraded.append(
                    {
                        "index": int(base_item.get("index", idx)),
                        "category": str(base_item.get("category", "General")),
                        "question": rewritten[idx],
                    }
                )

            return upgraded

    def active_model(self) -> Optional[str]:
        """Return currently active model identifier, if loaded."""
        return self._active_model_name
