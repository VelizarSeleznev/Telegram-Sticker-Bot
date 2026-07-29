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
        headers={"Content-Type": "application/json", **headers},
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
    if not isinstance(emojis, list) or len(emojis) != 3 or len(set(emojis)) != 3:
        raise ProviderError("response did not contain three distinct emoji")
    return {
        "emojis": [str(value) for value in emojis],
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
    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")}},
            ]
        }],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
            "thinkingConfig": {"thinkingLevel": thinking_level},
        },
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
    return openai_compatible(
        path=path,
        key_name="GROQ_API_KEY",
        url="https://api.groq.com/openai/v1/chat/completions",
        model="qwen/qwen3.6-27b",
        extra={"reasoning_effort": "none"},
    )


def openrouter(path: Path) -> tuple[str, dict[str, Any], float]:
    return openai_compatible(
        path=path,
        key_name="OPENROUTER_API_KEY",
        url="https://openrouter.ai/api/v1/chat/completions",
        model="openrouter/free",
    )


def openai(path: Path) -> tuple[str, dict[str, Any], float]:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ProviderError("OPENAI_API_KEY is missing")
    payload = {
        "model": "gpt-5.6-luna",
        "reasoning": {"effort": "none"},
        "max_output_tokens": 160,
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
    return str(response.get("model") or "gpt-5.6-luna"), parse_result(text), latency


PROVIDERS: dict[str, Callable[[Path], tuple[str, dict[str, Any], float]]] = {
    "gemini": gemini,
    "groq": groq,
    "openai": openai,
    "openrouter": openrouter,
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
