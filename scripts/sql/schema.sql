PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS posts (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  topic              TEXT NOT NULL,
  platform           TEXT NOT NULL CHECK(platform IN ('youtube','tiktok')),
  prompt_json_path   TEXT,
  video_path         TEXT,
  hook_key           TEXT,
  title              TEXT,
  description        TEXT,
  tags_csv           TEXT,
  scheduled_for_utc  TEXT,
  published_id       TEXT,
  status             TEXT NOT NULL DEFAULT 'ready'
);
CREATE INDEX IF NOT EXISTS idx_posts_ready ON posts(status, platform, scheduled_for_utc);

CREATE TABLE IF NOT EXISTS metrics (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  platform           TEXT NOT NULL,
  topic              TEXT NOT NULL,
  post_time_utc      TEXT,
  views              INTEGER DEFAULT 0,
  likes              INTEGER DEFAULT 0,
  comments           INTEGER DEFAULT 0,
  avg_view_duration_sec REAL DEFAULT 0,
  watch_time_sec     REAL DEFAULT 0,
  play_rate          REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS ab_hooks (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  topic              TEXT NOT NULL,
  hook_key           TEXT NOT NULL,
  hook_text          TEXT NOT NULL,
  ctr                REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS bandit_rewards (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  platform           TEXT NOT NULL,
  topic              TEXT NOT NULL,
  slot_time_utc      TEXT NOT NULL,
  hook_text          TEXT,
  reward             REAL NOT NULL
);
