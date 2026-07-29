from __future__ import annotations


def build_help_text() -> str:
    return (
        "Поддержка форматов (best-effort):\n"
        "Изображения: jpg/jpeg/png/webp/heic\n"
        "Видео: mp4/mov/webm/gif\n\n"
        "Режимы:\n"
        "- Квадрат: center crop + 512x512\n"
        "- Без обрезки: fit в 512x512\n\n"
        "Видео автоматически режется до 3 секунд и сжимается под лимит Telegram.\n"
        "Experimental 6 секунд включается один раз командой /video6 on; выключение: /video6 off.\n"
        "Можно приглашать редакторов в активный пак через /invite @username.\n\n"
        "Если что-то не так, пишите @ve_lizard или делайте pull request в репозиторий:\n"
        "https://github.com/VelizarSeleznev/Telegram-Sticker-Bot"
    )
