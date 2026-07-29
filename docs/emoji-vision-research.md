# Emoji vision provider research

Verified on 2026-07-29 against the two real 240x240 WebP inputs from production
jobs 901 and 902. The benchmark tool is
`scripts/benchmark_emoji_vision.py`; run it without copying keys into the repo:

```bash
ai-keys run -- python3 scripts/benchmark_emoji_vision.py image-1.webp image-2.webp
```

Override the Gemini model, thinking level, or output ceiling with
`GEMINI_MODEL`, `GEMINI_THINKING_LEVEL`, and
`GEMINI_MAX_OUTPUT_TOKENS`.

The prompt asks each model to OCR the meme, infer its meaning and reaction, and
return exactly three distinct Unicode emoji in JSON.

## Results

| Provider/model | Job 901 | Job 902 | Interpretation quality |
| --- | ---: | ---: | --- |
| Existing CLIP ViT-B/32 | local | local | `🔒 💬 🐵` and `🎮 😆 👏`; missed both meme meanings |
| Gemini 3.1 Flash-Lite | 1.08 s | 1.02 s | Correct OCR, aggression/rejection and "shut up" intent |
| Gemini 3.5 Flash-Lite | 15.11 s | 13.85 s | Correct, but highly variable; repeated identical inputs ranged from 0.85 s to a timeout |
| Gemini 3.6 Flash | 5.24 s | 16.03 s | Correct OCR, slower and no quality advantage for this classifier |
| GPT-5.6 Luna | 2.86 s | 5.63 s | First image correct; second OCR correct but visual interpretation drifted |
| OpenRouter free router | 9.88 s | 5.24 s | Correct with Gemma 4 26B, but routing and latency are not deterministic |
| Groq Qwen 3.6 27B | blocked | blocked | Both the existing and a newly issued Groq key list the model but receive HTTP 403; model permissions must be changed at the organization/project level |
| Cerebras public free API | unavailable | unavailable | Current public/free catalog is text-only; multimodal models are dedicated/coming soon |

These are end-to-end wall-clock measurements from Copenhagen on the current
free/test credentials, not provider token-generation claims. Exact provider
capacity and free-project queues can change.

## Gemini 3.1 reasoning levels

Retested the same two inputs with a 1,024-token output ceiling so hidden
thinking did not truncate the small JSON response:

| Thinking level | Job 901 | Job 902 | Quality change |
| --- | ---: | ---: | --- |
| `minimal` | 0.98 s | 0.87 s | Correct OCR and intent; best emoji set |
| `low` | 1.33 s | 1.29 s | Correct, no useful improvement |
| `medium` | 1.77 s | 1.71 s | Correct, no useful improvement |
| `high` | 2.38 s | 2.72 s | Correct, no useful improvement |

With the original 160-token ceiling, `low`, `medium`, and `high` consumed the
available output budget in hidden thinking and returned truncated or empty JSON.
For this classification task, keep `minimal` and the small ceiling. Raising
reasoning is only worth a separate fallback experiment on genuinely ambiguous
visual jokes.

## Recommendation

Use `gemini-3.1-flash-lite` with `thinkingLevel=minimal` as the first production
vision provider. It was both the fastest and the most accurate on the real
failure cases. Keep the model ID configurable so Groq Qwen 3.6 can be tested
again after its project model permission is enabled.

Do not keep the current CLIP result as the automatic choice. CLIP cannot OCR the
caption or infer a meme's intent, so it should only supply an instant placeholder
when the network model misses its deadline.

## Interaction design

1. Finish media conversion and persist the job.
2. Send the emoji-choice message immediately. Show original emoji when
   available, a neutral fallback otherwise, "Свой эмодзи", and
   "Не ждать AI".
3. Start one cancellable Gemini request concurrently with a 2.0-2.5 second
   deadline.
4. If it returns while the job is still waiting, atomically persist the three
   suggestions and edit the existing Telegram message and keyboard.
5. If the user chooses first, cancel/discard the provider result. Never delay
   sticker creation for a provider retry.
6. Cache successful results by
   `sha256(image) + provider + model + prompt_version`. For video, send one
   contact sheet made from representative frames so a single request sees the
   action and any captions.

The provider contract should return:

```json
{
  "emojis": ["🤫", "🤐", "💀"],
  "meaning": "short internal classification",
  "ocr": "visible text"
}
```

Validate that there are exactly three distinct standard Unicode emoji before
updating the job. Do not write images, OCR text, prompts, API keys, or provider
responses to production logs. Record only provider, model, prompt version,
success/failure class, cache hit, cancellation, and latency.

Gemini's free tier may use submitted data to improve Google products. That is
acceptable only if the two bot users understand that sticker images leave the
server. A paid tier has different data-use terms and should be considered if
private stickers become a real use case.
