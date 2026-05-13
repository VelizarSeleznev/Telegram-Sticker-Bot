#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${RUNTIME_DIR:-/home/egg/telegram-sticker-bot}"
workspace="${GITHUB_WORKSPACE:-$(pwd)}"

if [[ ! -f "$runtime_dir/.env" ]]; then
  echo "Missing runtime env file: $runtime_dir/.env" >&2
  exit 1
fi

rsync -a --delete \
  --exclude ".env" \
  --exclude ".git" \
  --exclude ".pytest_cache" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  "$workspace"/ "$runtime_dir"/

cd "$runtime_dir"

docker compose up -d --build sticker-bot
docker compose ps

docker compose exec -T sticker-bot python - <<'PY'
from app.bot.help_text import build_help_text

text = build_help_text()
required = [
    "@ve_lizard",
    "https://github.com/VelizarSeleznev/Telegram-Sticker-Bot",
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f"Help text verification failed, missing: {missing}")
PY
