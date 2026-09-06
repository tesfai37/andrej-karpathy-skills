"""SQLite layer. One connection, WAL, plain helpers - no ORM needed at this size."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS trials (
    key    TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    short  TEXT NOT NULL,
    emoji  TEXT NOT NULL DEFAULT '',
    sort   INTEGER NOT NULL DEFAULT 0,
    scored INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS achievements (
    key       TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    trial_key TEXT NOT NULL REFERENCES trials(key) ON DELETE CASCADE,
    kind      TEXT NOT NULL DEFAULT 'boss',   -- clear | boss | hm | title
    sort      INTEGER NOT NULL DEFAULT 0,
    requires  TEXT NOT NULL DEFAULT ''        -- space separated achievement keys
);

CREATE TABLE IF NOT EXISTS members (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id INTEGER UNIQUE,
    gamertag   TEXT NOT NULL COLLATE NOCASE UNIQUE,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS member_achievements (
    member_id       INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,            -- tank | healer | dps | account
    achievement_key TEXT NOT NULL REFERENCES achievements(key) ON DELETE CASCADE,
    granted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    granted_by      INTEGER,
    PRIMARY KEY (member_id, role, achievement_key)
);

CREATE TABLE IF NOT EXISTS scores (
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    trial_key   TEXT NOT NULL REFERENCES trials(key) ON DELETE CASCADE,
    score       INTEGER NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    recorded_by INTEGER,
    PRIMARY KEY (member_id, trial_key)
);

CREATE TABLE IF NOT EXISTS parses (
    member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    label       TEXT NOT NULL COLLATE NOCASE,
    dps         INTEGER NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    recorded_by INTEGER,
    PRIMARY KEY (member_id, label)
);

CREATE TABLE IF NOT EXISTS role_map (
    discord_role_id INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,   -- achievement | role
    value           TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL DEFAULT (datetime('now')),
    actor_id   INTEGER,
    actor_name TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    summary    TEXT NOT NULL,
    undo       TEXT,
    undone     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   INTEGER UNIQUE,
    channel_id   INTEGER,
    source_url   TEXT NOT NULL DEFAULT '',
    submitter_id INTEGER,
    member_id    INTEGER REFERENCES members(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT '',
    keys         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ma_member ON member_achievements(member_id);
CREATE INDEX IF NOT EXISTS idx_ma_key    ON member_achievements(achievement_key);
CREATE INDEX IF NOT EXISTS idx_audit_ts  ON audit_log(ts DESC);
"""


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None
        # One connection is shared by every command, so writes take a turn each -
        # otherwise two overlapping batches can commit half of each other's work.
        self._writing = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # --- tiny query helpers -------------------------------------------------
    async def all(self, sql: str, args: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def one(self, sql: str, args: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def val(self, sql: str, args: Sequence[Any] = (), default: Any = None) -> Any:
        """No row, or a NULL in the first column, both mean `default`."""
        row = await self.one(sql, args)
        return default if row is None or row[0] is None else row[0]

    async def run(self, sql: str, args: Sequence[Any] = ()) -> aiosqlite.Cursor:
        async with self._writing:
            cur = await self.conn.execute(sql, args)
            await self.conn.commit()
            return cur

    async def run_many(self, statements: Iterable[tuple[str, Sequence[Any]]]) -> None:
        async with self._writing:
            for sql, args in statements:
                await self.conn.execute(sql, args)
            await self.conn.commit()

    # --- settings -----------------------------------------------------------
    async def get_setting(self, key: str, default: Any = None) -> Any:
        raw = await self.val("SELECT value FROM settings WHERE key = ?", (key,))
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def set_setting(self, key: str, value: Any) -> None:
        await self.run(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    async def seed_catalog(self, catalog: dict) -> None:
        """Insert catalog rows that don't exist yet. Never overwrites admin edits."""
        for t in catalog["trials"]:
            await self.conn.execute(
                "INSERT OR IGNORE INTO trials(key, name, short, emoji, sort, scored) "
                "VALUES(?,?,?,?,?,?)",
                (t["key"], t["name"], t["short"], t.get("emoji", ""), t.get("sort", 0),
                 t.get("scored", 1)),
            )
        for a in catalog["achievements"]:
            await self.conn.execute(
                "INSERT OR IGNORE INTO achievements(key, name, trial_key, kind, sort, requires) "
                "VALUES(?,?,?,?,?,?)",
                (a["key"], a["name"], a["trial"], a.get("kind", "boss"), a.get("sort", 0),
                 a.get("requires", "")),
            )
        await self.conn.commit()
