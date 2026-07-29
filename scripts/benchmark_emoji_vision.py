#!/usr/bin/env python3
"""Compare vision APIs on real Telegram sticker images without logging keys."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


PROMPT = """\
You choose emoji tags for a Telegram sticker. Analyze the whole image as a meme:
read visible text, infer the joke, emotion, and intended reaction. Return exactly
three distinct standard Unicode emoji ordered from best to weakest. Prefer the
meme's meaning/reaction over merely naming an object in the picture.

Return JSON only:
{"emojis":["…","…","…"],"meaning":"short Russian explanation","ocr":"visible text or empty"}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "emojis": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "meaning": {"type": "string"},
        "ocr": {"type": "string"},
    },
    "required": ["emojis", "meaning", "ocr"],
    "additionalProperties": False,
}


class ProviderError(RuntimeError):
    pass


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/webp"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "otter-sticker-bot-emoji-benchmark/1",
            **headers,
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body)
            detail = parsed.get("error", parsed.get("message", {}))
            if isinstance(detail, dict):
                message = detail.get("message") or detail.get("type") or f"HTTP {exc.code}"
            else:
                message = str(detail) or f"HTTP {exc.code}"
        except Exception:
            message = f"HTTP {exc.code}"
        raise ProviderError(str(message)[:240]) from None
    except Exception as exc:
        raise ProviderError(f"{type(exc).__name__}: {exc}") from None
    return json.loads(body), time.perf_counter() - started


def parse_result(text: str) -> dict[str, Any]:
    result = json.loads(text)
    emojis = result.get("emojis")
    if not isinstance(emojis, list):
        raise ProviderError("response did not contain three distinct emoji")
    normalized = [str(value).strip() for value in emojis]
    if (
        len(normalized) != 3
        or any(not value for value in normalized)
        or len(set(normalized)) != 3
    ):
        raise ProviderError("response did not contain three distinct emoji")
    return {
        "emojis": normalized,
        "meaning": str(result.get("meaning", "")),
        "ocr": str(result.get("ocr", "")),
    }


def gemini(path: Path) -> tuple[str, dict[str, Any], float]:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ProviderError("GEMINI_API_KEY is missing")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    thinking_level = os.environ.get("GEMINI_THINKING_LEVEL", "minimal")
    max_output_tokens = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "160"))
    mime = mimetypes.guess_type(path.name)[0] or "image/webp"
    thinking_config: dict[str, Any] | None
    if model.startswith("gemini-2.5"):
        thinking_config = {"thinkingBudget": 0}
    elif model.startswith("gemma-"):
        thinking_config = None
    else:
        thinking_config = {"thinkingLevel": thinking_level}
    generation_config: dict[str, Any] = {
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
        "responseJsonSchema": SCHEMA,
    }
    if thinking_config is not None:
        generation_config["thinkingConfig"] = thinking_config
    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")}},
            ]
        }],
        "generationConfig": generation_config,
    }
    response, latency = post_json(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent",
        {"x-goog-api-key": key},
        payload,
    )
    parts = response["candidates"][0]["content"]["parts"]
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    return model, parse_result(text), latency


def openai_compatible(
    *,
    path: Path,
    key_name: str,
    url: str,
    model: str,
    extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], float]:
    key = os.environ.get(key_name, "")
    if not key:
        raise ProviderError(f"{key_name} is missing")
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_uri(path)}},
            ],
        }],
        "max_completion_tokens": 160,
        "response_format": {"type": "json_object"},
    }
    if extra:
        payload.update(extra)
    response, latency = post_json(url, {"Authorization": f"Bearer {key}"}, payload)
    choice = response["choices"][0]["message"]["content"]
    return str(response.get("model") or model), parse_result(choice), latency


def groq(path: Path) -> tuple[str, dict[str, Any], float]:
    reasoning_effort = os.environ.get("GROQ_REASONING_EFFORT", "none")
    max_output_tokens = int(os.environ.get("GROQ_MAX_OUTPUT_TOKENS", "160"))
    return openai_compatible(
        path=path,
        key_name="GROQ_API_KEY",
        url="https://api.groq.com/openai/v1/chat/completions",
        model="qwen/qwen3.6-27b",
        extra={
            "reasoning_effort": reasoning_effort,
            "max_completion_tokens": max_output_tokens,
        },
    )


def openrouter(path: Path) -> tuple[str, dict[str, Any], float]:
    model = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    max_output_tokens = int(os.environ.get("OPENROUTER_MAX_OUTPUT_TOKENS", "160"))
    reasoning_enabled = os.environ.get("OPENROUTER_REASONING_ENABLED")
    extra: dict[str, Any] = {"max_completion_tokens": max_output_tokens}
    if reasoning_enabled is not None:
        extra["reasoning"] = {
            "enabled": reasoning_enabled.strip().lower() in {"1", "true", "yes", "on"}
        }
    return openai_compatible(
        path=path,
        key_name="OPENROUTER_API_KEY",
        url="https://openrouter.ai/api/v1/chat/completions",
        model=model,
        extra=extra,
    )


def openai(path: Path) -> tuple[str, dict[str, Any], float]:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ProviderError("OPENAI_API_KEY is missing")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
    payload = {
        "model": model,
        "reasoning": {"effort": "none"},
        "max_output_tokens": int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "160")),
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_image", "image_url": data_uri(path), "detail": "original"},
            ],
        }],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "emoji_suggestions",
                "strict": True,
                "schema": SCHEMA,
            }
        },
    }
    response, latency = post_json(
        "https://api.openai.com/v1/responses",
        {"Authorization": f"Bearer {key}"},
        payload,
    )
    text = ""
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text += content.get("text", "")
    return str(response.get("model") or model), parse_result(text), latency


def anthropic(path: Path) -> tuple[str, dict[str, Any], float]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ProviderError("ANTHROPIC_API_KEY is missing")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    mime = mimetypes.guess_type(path.name)[0] or "image/webp"
    anthropic_schema = json.loads(json.dumps(SCHEMA))
    anthropic_schema["properties"]["emojis"].pop("minItems")
    anthropic_schema["properties"]["emojis"].pop("maxItems")
    payload = {
        "model": model,
        "max_tokens": int(os.environ.get("ANTHROPIC_MAX_OUTPUT_TOKENS", "160")),
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    },
                },
                {"type": "text", "text": PROMPT},
            ],
        }],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": anthropic_schema,
            }
        },
    }
    response, latency = post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        payload,
    )
    text = "".join(
        block.get("text", "")
        for block in response.get("content", [])
        if block.get("type") == "text"
    )
    return str(response.get("model") or model), parse_result(text), latency


PROVIDERS: dict[str, Callable[[Path], tuple[str, dict[str, Any], float]]] = {
    "gemini": gemini,
    "groq": groq,
    "openai": openai,
    "openrouter": openrouter,
    "anthropic": anthropic,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=list(PROVIDERS))
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    latencies: dict[str, list[float]] = {name: [] for name in args.providers}
    for repeat in range(args.repeats):
        for image in args.images:
            for provider in args.providers:
                try:
                    model, result, latency = PROVIDERS[provider](image)
                    latencies[provider].append(latency)
                    print(json.dumps({
                        "provider": provider,
                        "model": model,
                        "image": image.name,
                        "repeat": repeat + 1,
                        "latency_ms": round(latency * 1000),
                        **result,
                    }, ensure_ascii=False))
                except Exception as exc:
                    print(json.dumps({
                        "provider": provider,
                        "image": image.name,
                        "repeat": repeat + 1,
                        "error": str(exc),
                    }, ensure_ascii=False))

    for provider, values in latencies.items():
        if values:
            print(json.dumps({
                "provider": provider,
                "summary": True,
                "requests": len(values),
                "median_ms": round(statistics.median(values) * 1000),
                "min_ms": round(min(values) * 1000),
                "max_ms": round(max(values) * 1000),
            }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
