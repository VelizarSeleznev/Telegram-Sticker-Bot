#!/usr/bin/env python3
"""Isolate Groq Qwen vision request failures one request feature at a time."""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "qwen/qwen3.6-27b"
PUBLIC_IMAGE = "https://console.groq.com/og_cloud.png"
PRODUCTION_PROMPT = """\
You choose emoji tags for a Telegram sticker. Analyze the whole image as a meme:
read visible text, infer the joke, emotion, and intended reaction. Return exactly
three distinct standard Unicode emoji ordered from best to weakest. Prefer the
meme's meaning/reaction over merely naming an object in the picture.

Return JSON only:
{"emojis":["…","…","…"],"meaning":"short Russian explanation","ocr":"visible text or empty"}
"""
STRICT_OCR_PROMPT = """\
You choose emoji tags for a Telegram sticker. First transcribe every visible
word literally, preserving the original language and profanity. Do not censor,
translate, correct, or replace text based on objects in the picture. Then infer
the meme's intended reaction from that literal text and the image together.
Return exactly three distinct standard Unicode emoji ordered best first.

Return JSON only:
{"emojis":["…","…","…"],"meaning":"short Russian explanation","ocr":"exact visible text"}
"""


def image_part(url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}}


def local_jpeg(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def run_case(name: str, payload: dict[str, Any], key: str) -> None:
    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "sticker-bot-groq-diagnostic/1",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.load(response)
            print(json.dumps({
                "case": name,
                "status": response.status,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model": body.get("model"),
                "content": body.get("choices", [{}])[0].get("message", {}).get("content", "")[:160],
            }, ensure_ascii=False))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            error: Any = json.loads(raw)
        except Exception:
            error = raw.decode("utf-8", errors="replace")[:300] or None
        print(json.dumps({
            "case": name,
            "status": exc.code,
            "reason": exc.reason,
            "content_type": exc.headers.get("content-type"),
            "request_id": exc.headers.get("x-request-id"),
            "server": exc.headers.get("server"),
            "error": error,
        }, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jpeg", type=Path)
    parser.add_argument("--crop-jpeg", type=Path)
    parser.add_argument("--case", action="append", dest="selected_cases")
    args = parser.parse_args()
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise SystemExit("GROQ_API_KEY is missing")

    base = {"model": MODEL, "max_completion_tokens": 160}
    cases = [
        ("text-basic", {
            **base,
            "messages": [{"role": "user", "content": "Reply with the word OK."}],
        }),
        ("text-json", {
            **base,
            "messages": [{"role": "user", "content": 'Return only {"ok":true}.'}],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
        }),
        ("public-image-basic", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Name the main colors in this image."},
                image_part(PUBLIC_IMAGE),
            ]}],
        }),
        ("public-image-json", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": 'Return JSON only: {"colors":["color"]}.'},
                image_part(PUBLIC_IMAGE),
            ]}],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
        }),
        ("local-jpeg-basic", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Read the visible text and explain the intended reaction."},
                image_part(local_jpeg(args.jpeg)),
            ]}],
        }),
        ("local-jpeg-json", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": 'Return JSON only: {"emojis":["…","…","…"],"ocr":"text"}.'},
                image_part(local_jpeg(args.jpeg)),
            ]}],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
        }),
        ("local-production-short", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": (
                    "Read the visible text and infer the intended reaction. "
                    'Return JSON only: {"emojis":["…","…","…"],"meaning":"short","ocr":"text"}.'
                )},
                image_part(local_jpeg(args.jpeg)),
            ]}],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
        }),
        ("local-production-original", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PRODUCTION_PROMPT},
                image_part(local_jpeg(args.jpeg)),
            ]}],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
        }),
        ("local-production-strict-ocr", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": STRICT_OCR_PROMPT},
                image_part(local_jpeg(args.jpeg)),
            ]}],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
        }),
    ]
    if args.crop_jpeg:
        cases.append(("local-production-with-ocr-crop", {
            **base,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": STRICT_OCR_PROMPT},
                image_part(local_jpeg(args.jpeg)),
                image_part(local_jpeg(args.crop_jpeg)),
            ]}],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
        }))
    for name, payload in cases:
        if not args.selected_cases or name in args.selected_cases:
            run_case(name, payload, key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
