import pytest

from app.config import Settings


def test_settings_require_gemini_api_key(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        Settings.from_env()


def test_settings_default_to_gemma_4(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:test")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("EMOJI_VISION_MODEL", raising=False)

    settings = Settings.from_env()

    assert settings.emoji_vision_model == "gemma-4-26b-a4b-it"
    assert settings.emoji_vision_timeout_seconds == 30
    assert settings.emoji_vision_max_output_tokens == 192
