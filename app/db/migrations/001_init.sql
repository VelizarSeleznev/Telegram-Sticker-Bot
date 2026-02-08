PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_user_id INTEGER NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS packs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  short_name TEXT NOT NULL,
  tg_set_name TEXT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('draft', 'ready')),
  is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_packs_one_active_per_user
ON packs(user_id) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS media_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  telegram_file_id TEXT NOT NULL,
  telegram_file_unique_id TEXT NOT NULL,
  media_kind TEXT NOT NULL CHECK (media_kind IN ('image', 'video')),
  mime TEXT NULL,
  original_name TEXT NULL,
  temp_path TEXT NOT NULL,
  crop_mode TEXT NULL CHECK (crop_mode IN ('square', 'fit')),
  processed_path TEXT NULL,
  preview_path TEXT NULL,
  suggestions_json TEXT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'crop_chosen', 'emoji_chosen', 'done', 'error')),
  error_text TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_media_jobs_user_status ON media_jobs(user_id, status);

CREATE TABLE IF NOT EXISTS stickers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id INTEGER NOT NULL,
  media_kind TEXT NOT NULL CHECK (media_kind IN ('image', 'video')),
  emoji TEXT NOT NULL,
  telegram_file_id TEXT NULL,
  source_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE
);
