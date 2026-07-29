PRAGMA foreign_keys = ON;

ALTER TABLE users
ADD COLUMN video_duration_seconds INTEGER NOT NULL DEFAULT 3
CHECK (video_duration_seconds IN (3, 6));
