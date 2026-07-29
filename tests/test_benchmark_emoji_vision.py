import pytest

from scripts.benchmark_emoji_vision import ProviderError, parse_result


def test_parse_result_trims_emoji_values() -> None:
    result = parse_result(
        '{"emojis":[" 🤫 ","🤐","👊"],"meaning":"замолчи","ocr":"текст"}'
    )

    assert result["emojis"] == ["🤫", "🤐", "👊"]


@pytest.mark.parametrize(
    "emojis",
    [
        '["👀", "", "🤨"]',
        '["😱", " ", "🤯"]',
        '["🤫", "🤫", "👊"]',
    ],
)
def test_parse_result_rejects_blank_or_duplicate_values(emojis: str) -> None:
    with pytest.raises(ProviderError, match="three distinct emoji"):
        parse_result(f'{{"emojis":{emojis},"meaning":"","ocr":""}}')
