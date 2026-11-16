"""The official Gemini Developer REST endpoint with no automatic retries or tools."""

from dataclasses import dataclass
import json
from time import monotonic

import httpx

from .grounding import SYSTEM

FREE_MODELS = frozenset({"gemini-3.1-flash-lite"})
SCHEMA = {
    "type": "object",
    "properties": {
        "disposition": {"type": "string", "enum": ["draft", "abstain", "escalate"]},
        "sentence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": ["disposition", "sentence_ids"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ProviderResult:
    output: str | None = None
    category: str | None = None
    usage: dict | None = None
    latency_ms: int = 0


class Gemini:
    def __init__(self, settings, transport=None):
        self.model = settings.gemini_model
        self._key = settings.gemini_api_key
        self.timeout = settings.gemini_timeout_seconds
        self._transport = transport
        self.available = bool(settings.gemini_free_tier_confirmed and self._key
                              and self._key.get_secret_value().strip() and self.model in FREE_MODELS)

    def generate(self, payload):
        if not self.available:
            return ProviderResult(category="PROVIDER_DISABLED")
        if len(payload.encode("utf-8")) > 20_000:
            return ProviderResult(category="REQUEST_TOO_LARGE")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        config = {
            "temperature": 0.0, "candidateCount": 1, "maxOutputTokens": 512,
            "responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        }
        started = monotonic()

        def result(**fields):
            return ProviderResult(latency_ms=round((monotonic() - started) * 1000), **fields)

        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=5), transport=self._transport,
                              follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", url, headers={
                    "x-goog-api-key": self._key.get_secret_value(), "Accept-Encoding": "identity",
                }, json={
                    "systemInstruction": {"parts": [{"text": SYSTEM}]},
                    "contents": [{"role": "user", "parts": [{"text": payload}]}],
                    "generationConfig": config,
                }) as response:
                    if response.status_code == 429:
                        return result(category="PROVIDER_QUOTA")
                    if response.status_code in (500, 502, 503, 504):
                        return result(category="PROVIDER_TEMPORARY")
                    if response.status_code != 200:
                        return result(category="PROVIDER_REJECTED")
                    data = bytearray()
                    for part in response.iter_bytes(chunk_size=4096):
                        data.extend(part)
                        if len(data) > 32_768 or monotonic() - started > self.timeout:
                            return result(category="PROVIDER_INVALID")
            envelope = json.loads(data)
            meta = envelope.get("usageMetadata") or {}
            usage = {key: meta[key] for key in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount")
                     if type(meta.get(key)) is int and 0 <= meta[key] <= 1_000_000}
            candidate, = envelope.get("candidates") or [{}]
            if candidate.get("finishReason") != "STOP":
                return result(category="PROVIDER_NO_DRAFT", usage=usage)
            parts = candidate["content"]["parts"]
            output = "".join(part["text"] for part in parts if not part.get("thought"))
            if not output or len(output) > 4000:
                return result(category="PROVIDER_INVALID", usage=usage)
            return result(output=output, usage=usage)
        except httpx.ConnectTimeout:
            return result(category="PROVIDER_TEMPORARY")
        except httpx.HTTPError:
            # A read or transport failure can be ambiguous: do not retry it.
            return result(category="PROVIDER_NETWORK")
        except (ValueError, TypeError, KeyError, AttributeError):
            return result(category="PROVIDER_INVALID")
