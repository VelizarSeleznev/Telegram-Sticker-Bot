PRAGMA foreign_keys = ON;

ALTER TABLE users ADD COLUMN username_lc TEXT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_lc ON users(username_lc) WHERE username_lc IS NOT NULL;

CREATE TABLE IF NOT EXISTS pack_members (
  pack_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner', 'editor')),
  invited_by_user_id INTEGER NULL,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  PRIMARY KEY (pack_id, user_id),
  FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (invited_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pack_members_user ON pack_members(user_id);
CREATE INDEX IF NOT EXISTS idx_pack_members_pack_role ON pack_members(pack_id, role);

CREATE TABLE IF NOT EXISTS pack_invitations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id INTEGER NOT NULL,
  inviter_user_id INTEGER NOT NULL,
  invited_username_lc TEXT NOT NULL,
  invited_user_id INTEGER NULL,
  token TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
  expires_at TEXT NOT NULL,
  accepted_by_user_id INTEGER NULL,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE,
  FOREIGN KEY (inviter_user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (invited_user_id) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (accepted_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pack_invitations_pack_status ON pack_invitations(pack_id, status);
CREATE INDEX IF NOT EXISTS idx_pack_invitations_username_status ON pack_invitations(invited_username_lc, status);
CREATE INDEX IF NOT EXISTS idx_pack_invitations_token ON pack_invitations(token);

CREATE TABLE IF NOT EXISTS user_active_packs (
  user_id INTEGER PRIMARY KEY,
  pack_id INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (pack_id) REFERENCES packs(id) ON DELETE CASCADE
);

INSERT OR IGNORE INTO pack_members(pack_id, user_id, role, invited_by_user_id, created_at, updated_at)
SELECT id, user_id, 'owner', NULL, created_at, updated_at FROM packs;

INSERT OR REPLACE INTO user_active_packs(user_id, pack_id, updated_at)
SELECT user_id, id, updated_at FROM packs WHERE is_active = 1;
