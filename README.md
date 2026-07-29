# Telegram Sticker Bot (MVP)

Бот для создания и ведения собственных стикерпаков в Telegram:
- static стикеры (изображения),
- video стикеры (видео/гиф),
- импорт стикера из другого пака с выбором эмодзи (авто/исходный),
- авто-эмодзи (локально, без внешнего API),
- обработка без обрезки по умолчанию с необязательной кнопкой `обрезать до квадрата`,
- выбор эмодзи кнопкой или простым сообщением с нужным эмодзи,
- черновик пака до первого стикера,
- несколько паков + переключение активного,
- совместное редактирование пака (owner/editor) через инвайты.
- inline-поиск GIF через Klipy: `@otter_sticker_bot запрос`.

## Стек
- Python 3.12
- aiogram 3
- SQLite
- ffmpeg
- Pillow + pillow-heif
- CLIP (transformers + torch) для авто-эмодзи

Исследование замены CLIP на быстрое vision-распознавание и воспроизводимый
benchmark: [`docs/emoji-vision-research.md`](docs/emoji-vision-research.md).

## Команды
- `/start`
- `/newpack`
- `/packs`
- `/setactive [pack_id]`
- `/invite @username`
- `/members`
- `/kick [member_id]`
- `/video6 [on|off]`
- `/help`
- `/cancel`

## Support
- If something is off, contact `@ve_lizard` or open a pull request in the repository: https://github.com/VelizarSeleznev/Telegram-Sticker-Bot

## Быстрый запуск (Docker Compose)
1. Скопируйте `.env.example` в `.env` и заполните значения.
2. Запустите:
   ```bash
   docker compose up -d --build
   ```
3. Логи:
   ```bash
   docker compose logs -f sticker-bot
   ```

## Переменные окружения
- `BOT_TOKEN` - токен бота от BotFather
- `DB_PATH` - путь к SQLite (по умолчанию `/data/bot.db`)
- `TEMP_DIR` - временная папка для обработки медиа
- `LOG_LEVEL` - `INFO|DEBUG|WARN`
- `MAX_CONCURRENT_JOBS` - число параллельных конвертаций
- `POLLING_TIMEOUT` - timeout long polling
- `KLIPY_API_KEY` - ключ Klipy API для inline-поиска GIF
- `KLIPY_CLIENT_KEY` - client key для Klipy, по умолчанию `otter_sticker_bot`
- `KLIPY_LOCALE` - локаль поиска Klipy, по умолчанию `ru_RU`
- `KLIPY_COUNTRY` - country hint для Klipy, по умолчанию `US`
- `KLIPY_CONTENT_FILTER` - фильтр контента Klipy, по умолчанию `medium`

## Локальный запуск без Docker
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполните .env
python -m app.main
```

## Поведение media pipeline
- Изображения и импортируемые static-стикеры: длинная сторона масштабируется вверх или вниз до `512 px`, затем результат центрируется на прозрачном холсте `512x512` и сохраняется в `webp`; `center crop` до квадрата доступен кнопкой на шаге выбора эмодзи для новых изображений.
- Видео: по умолчанию `fit`, авто-трим до `3s`, без аудио, `VP9/webm`; двухпроходный подбор битрейта сохраняет холст `512x512` и укладывает файл в `256 КБ`, затем `ffprobe` проверяет результат перед отправкой.
- Experimental-режим `/video6 on` сохраняется в профиле пользователя и обрабатывает следующие видео длительностью до `6s`, подменяя WebM Duration. Это неофициальный обход лимита Telegram; `/video6 off` возвращает надежные `3s`.
- После добавления бот отправляет готовый стикер в чат, если Telegram принимает локальный файл как sticker-preview.
- На шаге выбора эмодзи можно нажать предложенный вариант или просто отправить нужный эмодзи сообщением.
- Если Telegram отклоняет формат для текущего пака: бот сообщает ошибку и просит сменить/создать пак.

## Бэкап/восстановление
В Docker Compose:
- состояние хранится в volume `bot_data` (`/data/bot.db`),
- для переноса на другой сервер достаточно перенести проект + `.env` + backup volume.

## Деплой на `seggver`
- GitHub Actions workflow `.github/workflows/deploy-seggver.yml` запускается на self-hosted runner `seggver-sticker-bot`.
- Runtime-путь на сервере: `/home/egg/telegram-sticker-bot`.
- Deploy script: `scripts/deploy_seggver.sh`; он синхронизирует checkout в runtime-путь, сохраняет серверный `.env`, пересобирает `docker compose` и проверяет help-текст внутри контейнера.

## Надежность старта
- На старте бот проверяет `getMe()` с повтором при transient `TelegramNetworkError`, чтобы краткие DNS/Telegram-сбои не валили контейнер.

## Замечания
- MVP работает только в личных чатах.
- Animated `.tgs` в MVP не поддерживается.
- Решения и ограничения media pipeline описаны в [`docs/media-pipeline.md`](docs/media-pipeline.md).
