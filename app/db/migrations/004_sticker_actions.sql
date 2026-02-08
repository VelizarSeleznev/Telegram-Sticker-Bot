PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sticker_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  action_token TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('delete', 'import')),
  sticker_file_id TEXT NOT NULL,
  sticker_file_unique_id TEXT NOT NULL,
  sticker_set_name TEXT NULL,
  original_emoji TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  expires_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sticker_actions_user ON sticker_actions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_sticker_actions_token ON sticker_actions(action_token);
