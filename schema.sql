CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS checkins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    log_date      TEXT    NOT NULL,                 -- YYYY-MM-DD
    energy        INTEGER NOT NULL CHECK (energy BETWEEN 1 AND 5),
    mood          INTEGER NOT NULL CHECK (mood   BETWEEN 1 AND 5),
    focus         INTEGER NOT NULL CHECK (focus  BETWEEN 1 AND 5),
    focused_hours REAL    NOT NULL DEFAULT 0 CHECK (focused_hours BETWEEN 0 AND 24),
    accomplished  TEXT,
    wins          TEXT,
    blockers      TEXT,
    priorities    TEXT,                             -- one per line
    score         INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, log_date)
);

CREATE TABLE IF NOT EXISTS habits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    icon       TEXT    NOT NULL DEFAULT '✅',
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS habit_logs (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    log_date TEXT    NOT NULL,
    PRIMARY KEY (habit_id, log_date)
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    description TEXT,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('planned', 'active', 'paused', 'complete')),
    next_action TEXT,
    github_url  TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id   INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title        TEXT    NOT NULL,
    notes        TEXT,
    priority     INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
    due_date     TEXT,
    status       TEXT    NOT NULL DEFAULT 'todo'
                 CHECK (status IN ('todo', 'doing', 'done')),
    completed_at TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS life_areas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    icon       TEXT    NOT NULL DEFAULT '📁',
    color      TEXT    NOT NULL DEFAULT '#4a9eff',
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    area_id     INTEGER REFERENCES life_areas(id) ON DELETE SET NULL,
    title       TEXT    NOT NULL,
    why         TEXT,
    target_date TEXT,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'achieved', 'paused', 'dropped')),
    progress    INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weekly_reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_start TEXT    NOT NULL,                  -- the Monday of that week
    rating     INTEGER CHECK (rating BETWEEN 1 AND 5),
    went_well  TEXT,
    challenges TEXT,
    lessons    TEXT,
    next_focus TEXT,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, week_start)
);

CREATE TABLE IF NOT EXISTS user_achievements (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code        TEXT    NOT NULL,
    unlocked_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, code)
);


-- Phones/browsers that turned on daily reminders (one row per device)
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint   TEXT    NOT NULL UNIQUE,     -- the device's private delivery address
    p256dh     TEXT    NOT NULL,            -- encryption keys from the browser
    auth       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_id);

-- Feedback people send from the app (ideas, problems, things they love)
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL CHECK (kind IN ('idea', 'problem', 'love')),
    message    TEXT    NOT NULL,
    page       TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Private analytics: which days each person used the app (nothing else)
CREATE TABLE IF NOT EXISTS activity_days (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day     TEXT    NOT NULL,          -- YYYY-MM-DD on the person's own clock
    PRIMARY KEY (user_id, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_activity_day ON activity_days(day);

-- Small settings the app keeps for itself (e.g. the date of the last backup)
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Evenings where someone tapped "Close my day"
CREATE TABLE IF NOT EXISTS day_closes (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day     TEXT    NOT NULL,                 -- YYYY-MM-DD on their own clock
    PRIMARY KEY (user_id, day)
) WITHOUT ROWID;

-- "Quit" habits: days the person slipped (everything else counts as clean)
CREATE TABLE IF NOT EXISTS habit_slips (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    log_date TEXT    NOT NULL,
    PRIMARY KEY (habit_id, log_date)
) WITHOUT ROWID;

-- Days protected by a streak freeze
CREATE TABLE IF NOT EXISTS frozen_days (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day     TEXT    NOT NULL,
    PRIMARY KEY (user_id, day)
) WITHOUT ROWID;

-- Focus timer sessions (minutes are filled in when the session ends)
CREATE TABLE IF NOT EXISTS focus_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    label           TEXT    NOT NULL DEFAULT '',
    planned_minutes INTEGER NOT NULL CHECK (planned_minutes BETWEEN 1 AND 180),
    started_at      TEXT    NOT NULL,          -- UTC, e.g. 2026-11-22T15:04:05+00:00
    log_date        TEXT    NOT NULL,          -- the day it counts for, on the person's clock
    ended_at        TEXT,
    minutes         INTEGER,
    notified        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_focus_user ON focus_sessions(user_id, log_date);

-- "Was this insight helpful?" (not helpful = hidden for that person)
CREATE TABLE IF NOT EXISTS insight_votes (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    insight_key TEXT    NOT NULL,
    helpful     INTEGER NOT NULL CHECK (helpful IN (0, 1)),
    voted_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, insight_key)
) WITHOUT ROWID;

-- An uploaded file waiting for "Import" on the preview page (deleted after 2 hours)
CREATE TABLE IF NOT EXISTS import_drafts (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token      TEXT    NOT NULL,
    plan       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, token)
) WITHOUT ROWID;
