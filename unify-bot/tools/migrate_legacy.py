"""Move the old wide-table database.db into the new schema.

    python tools/migrate_legacy.py /path/to/old/database.db

Old layout: one table per role (TANK / HEALER / DPS / AWA) with a GamerTag
column, a DiscordName column, and one column per achievement holding 'X'.
Plus SCORE and PARSE tables in the same shape with numbers in them.

Nothing is deleted from the old file - it is only read."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unify.db import Database  # noqa: E402
from unify.importer import cell_is_true, cell_number, normalize  # noqa: E402

ROLE_TABLES = {"TANK": "tank", "HEALER": "healer", "DPS": "dps", "AWA": "account"}


def read_table(old: sqlite3.Connection, table: str) -> tuple[list[str], list[sqlite3.Row]]:
    try:
        rows = old.execute(f'SELECT * FROM "{table}"').fetchall()
    except sqlite3.OperationalError:
        return [], []
    columns = [d[0] for d in old.execute(f'SELECT * FROM "{table}" LIMIT 0').description]
    return columns, rows


def column_index(columns: list[str], *wanted: str) -> int:
    for i, name in enumerate(columns):
        if normalize(name) in wanted:
            return i
    return -1


async def migrate(old_path: str, new_path: str) -> None:
    old = sqlite3.connect(old_path)
    db = Database(new_path)
    await db.connect()
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "unify" / "data" / "catalog.json").read_text()
    )
    await db.seed_catalog(catalog)

    keys = {r[0] for r in await db.all("SELECT key FROM achievements")}
    trials = {r[0] for r in await db.all("SELECT key FROM trials")}
    member_ids: dict[str, int] = {}

    async def member_id(gamertag: str, discord_name: str | None) -> int | None:
        gamertag = (gamertag or "").strip()
        if not gamertag:
            return None
        if gamertag.lower() in member_ids:
            return member_ids[gamertag.lower()]
        existing = await db.val("SELECT id FROM members WHERE gamertag = ?", (gamertag,))
        if existing is None:
            cur = await db.run("INSERT INTO members(gamertag) VALUES(?)", (gamertag,))
            existing = cur.lastrowid
        member_ids[gamertag.lower()] = existing
        return existing

    stats = {"members": 0, "achievements": 0, "scores": 0, "parses": 0, "unknown_columns": set()}

    for table, role in ROLE_TABLES.items():
        columns, rows = read_table(old, table)
        if not columns:
            print(f"  {table}: not present, skipped")
            continue
        tag_col = column_index(columns, "gamertag", "tag")
        name_col = column_index(columns, "discordname", "discord")
        if tag_col < 0:
            print(f"  {table}: no GamerTag column, skipped")
            continue
        value_cols = {i: normalize(c) for i, c in enumerate(columns) if normalize(c) in keys}
        stats["unknown_columns"] |= {
            c for i, c in enumerate(columns)
            if i not in value_cols and i not in (tag_col, name_col) and c
        }
        statements = []
        for row in rows:
            mid = await member_id(row[tag_col], row[name_col] if name_col >= 0 else None)
            if mid is None:
                continue
            for i, key in value_cols.items():
                if cell_is_true(row[i]):
                    statements.append((
                        "INSERT OR IGNORE INTO member_achievements"
                        "(member_id, role, achievement_key) VALUES(?,?,?)", (mid, role, key)))
        await db.run_many(statements)
        stats["achievements"] += len(statements)
        print(f"  {table}: {len(rows)} rows -> {len(statements)} achievements ({role})")

    columns, rows = read_table(old, "SCORE")
    if columns:
        tag_col = column_index(columns, "gamertag", "tag")
        value_cols = {i: normalize(c) for i, c in enumerate(columns) if normalize(c) in trials}
        statements = []
        for row in rows:
            mid = await member_id(row[tag_col], None) if tag_col >= 0 else None
            if mid is None:
                continue
            for i, trial in value_cols.items():
                value = cell_number(row[i])
                if value:
                    statements.append((
                        "INSERT OR REPLACE INTO scores(member_id, trial_key, score) "
                        "VALUES(?,?,?)", (mid, trial, value)))
        await db.run_many(statements)
        stats["scores"] = len(statements)
        print(f"  SCORE: {len(rows)} rows -> {len(statements)} scores")

    columns, rows = read_table(old, "PARSE")
    if columns:
        tag_col = column_index(columns, "gamertag", "tag")
        name_col = column_index(columns, "discordname", "discord")
        statements = []
        for row in rows:
            mid = await member_id(row[tag_col], None) if tag_col >= 0 else None
            if mid is None:
                continue
            for i, column in enumerate(columns):
                if i in (tag_col, name_col) or not column:
                    continue
                value = cell_number(row[i])
                if value:
                    statements.append((
                        "INSERT OR REPLACE INTO parses(member_id, label, dps) VALUES(?,?,?)",
                        (mid, column.strip(), value)))
        await db.run_many(statements)
        stats["parses"] = len(statements)
        print(f"  PARSE: {len(rows)} rows -> {len(statements)} parses")

    stats["members"] = await db.val("SELECT COUNT(*) FROM members", (), 0)
    await db.close()
    old.close()

    print(f"\nDone. {stats['members']} members, {stats['achievements']} achievements, "
          f"{stats['scores']} scores, {stats['parses']} parses.")
    if stats["unknown_columns"]:
        print("\nColumns I did not recognise (nothing was lost, they were just skipped):")
        print("  " + ", ".join(sorted(stats["unknown_columns"])))
        print("Add them with /achievement new, then re-run this, or fix them with /import.")
    print("\nDiscord accounts are not carried over - the old file only had display names. "
          "Link people with /member link, or /import a sheet with a DiscordName column of IDs.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python tools/migrate_legacy.py <old database.db> [new db path]")
    asyncio.run(migrate(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "data/unify.sqlite3"))
