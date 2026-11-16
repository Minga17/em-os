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

