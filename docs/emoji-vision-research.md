# Emoji vision provider research

Verified on 2026-07-29 against the two real 240x240 WebP inputs from production
jobs 901 and 902. The benchmark tool is
`scripts/benchmark_emoji_vision.py`; run it without copying keys into the repo:

```bash
ai-keys run -- python3 scripts/benchmark_emoji_vision.py image-1.webp image-2.webp
```

Override the Gemini model, thinking level, or output ceiling with
`GEMINI_MODEL`, `GEMINI_THINKING_LEVEL`, and
`GEMINI_MAX_OUTPUT_TOKENS`. Select a fixed OpenRouter model with
`OPENROUTER_MODEL`. Groq reasoning and output size are controlled by
`GROQ_REASONING_EFFORT` and `GROQ_MAX_OUTPUT_TOKENS`. OpenAI and Anthropic
model IDs can be changed with `OPENAI_MODEL` and `ANTHROPIC_MODEL`.

The prompt asks each model to OCR the meme, infer its meaning and reaction, and
return exactly three distinct Unicode emoji in JSON.

## Results

| Provider/model | Job 901 | Job 902 | Interpretation quality |
| --- | ---: | ---: | --- |
| Existing CLIP ViT-B/32 | local | local | `🔒 💬 🐵` and `🎮 😆 👏`; missed both meme meanings |
| Gemini 3.1 Flash-Lite, minimal | 0.98 s | 0.87 s | Correct OCR, aggression/rejection and "shut up" intent |
| Groq Qwen 3.6 27B, no reasoning | 0.56 s | 0.52 s | Fastest; first image correct, second hallucinated a command to close a door |
| Groq Qwen 3.6 27B, reasoning | not rerun | 2.01 s | Still wrong: read only "ЗАКРОЙ" and inferred closing Minecraft |
| Gemini 2.5 Flash-Lite | 2.04 s | 1.10 s | Second image had bad OCR and wrong meaning |
| Gemini 3.5 Flash-Lite | 15.11 s | 13.85 s | Correct, but highly variable; repeated identical inputs ranged from 0.85 s to a timeout |
| Gemini 3.5 Flash | 9.11 s | 10.70 s | Correct, but too slow |
| Gemini 3.6 Flash | 5.24 s | 16.03 s | Correct OCR, slower and no quality advantage for this classifier |
| GPT-5.6 Luna | 2.86 s | 5.63 s | First image correct; second OCR correct but visual interpretation drifted |
| GPT-5.4 Nano | not rerun | 2.36 s | Wrong OCR and meaning |
| GPT-5.4 Mini | not rerun | 1.89 s | Wrong OCR and meaning |
| Claude Haiku 4.5 | 4.88 s | 2.66 s | Both meanings wrong; second OCR was wrong |
| Gemma 4 26B direct | not rerun | 3.61 s | Correct OCR and meaning |
| Gemma 4 31B direct | not rerun | 3.29 s | Correct OCR and meaning |
| OpenRouter free router | 9.88 s | 5.24 s | Correct with Gemma 4 26B, but routing and latency are not deterministic |
| OpenRouter Gemma 4 26B free | not rerun | 10.03 s | Correct, but much slower than Google direct |
| OpenRouter Nemotron Nano 12B free | not rerun | 2.37 s | Wrong OCR and meaning |
| OpenRouter Nemotron Omni 30B free | not rerun | 11.03 s | Wrong OCR and meaning |
| Cerebras public free API | unavailable | unavailable | Current public/free catalog is text-only; multimodal models are dedicated/coming soon |

These are end-to-end wall-clock measurements from Copenhagen on the current
free/test credentials, not provider token-generation claims. Exact provider
capacity and free-project queues can change.

## Groq 403 root cause and quota

The original Groq failure was a benchmark-client bug, not a key or model
permission problem. `urllib` sent its default `Python-urllib` User-Agent and
Groq's edge returned an empty HTTP 403. Adding an ordinary application
User-Agent made the otherwise identical multimodal request succeed. The
diagnostic script in `scripts/diagnose_groq_vision.py` preserves the one-variable
control cases that found this.

The current Qwen quota is not unlimited even though access is enabled. The
console shows 30 requests/minute, 1,000 requests/day, 8,000 tokens/minute, and
200,000 tokens/day for Qwen 3.6. A single 240x240 benchmark image was charged
about 2,580 tokens, so the 8,000 TPM bucket became the effective limit at roughly
three such image requests in a rolling minute. The API correctly returned 429
until that bucket replenished.

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
vision provider. It was the fastest model that correctly understood both real
failure cases. Groq Qwen 3.6 is about 0.4 seconds faster, but that saving is not
worth silently attaching the wrong emoji to Russian text memes. Extra Groq
reasoning made the hard case four times slower without fixing it.

If a sub-second speculative preview is useful, Groq can populate temporary
choices first and Gemini can replace them when its result arrives. It must not
be treated as a quality fallback, and 429 responses should fall through to
Gemini immediately. With only two bot users, the simpler and more reliable
choice is to call Gemini directly and keep the interaction cancellable.

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
