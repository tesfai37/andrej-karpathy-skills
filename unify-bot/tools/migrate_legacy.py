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

import collections  # noqa: E402

from unify.db import Database  # noqa: E402
from unify.importer import cell_is_blank, cell_mark, cell_number, normalize  # noqa: E402

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

    seen_rows: collections.Counter = collections.Counter()
    current_table = [""]          # counted per sheet, not across all six

    async def member_id(gamertag: str, discord_name: str | None) -> int | None:
        gamertag = (gamertag or "").strip()
        if not gamertag:
            return None
        seen_rows[(current_table[0], gamertag.lower())] += 1
        if gamertag.lower() in member_ids:
            return member_ids[gamertag.lower()]
        existing = await db.val("SELECT id FROM members WHERE gamertag = ?", (gamertag,))
        if existing is None:
            cur = await db.run("INSERT INTO members(gamertag, legacy_name) VALUES(?,?)",
                               (gamertag, (discord_name or "").strip()))
            existing = cur.lastrowid
        elif discord_name and str(discord_name).strip():
            await db.run("UPDATE members SET legacy_name = ? WHERE id = ? AND legacy_name = ''",
                         (str(discord_name).strip(), existing))
        member_ids[gamertag.lower()] = existing
        return existing

    stats = {"members": 0, "achievements": 0, "marked": 0, "scores": 0, "parses": 0,
             "disagreements": 0,
             "unknown_columns": set(), "unreadable": collections.Counter()}

    for table, role in ROLE_TABLES.items():
        current_table[0] = table
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
        marks: dict[tuple[int, str], str] = {}
        for row in rows:
            mid = await member_id(row[tag_col], row[name_col] if name_col >= 0 else None)
            if mid is None:
                continue
            for i, key in value_cols.items():
                mark = cell_mark(row[i])
                if mark:
                    if marks.get((mid, key), mark) != mark:
                        stats["disagreements"] += 1     # duplicate rows, different marks
                    marks.setdefault((mid, key), mark)
                elif not cell_is_blank(row[i]):
                    stats["unreadable"][str(row[i]).strip()] += 1
        await db.run_many([
            ("INSERT OR IGNORE INTO member_achievements"
             "(member_id, role, achievement_key, mark) VALUES(?,?,?,?)", (mid, role, key, mark))
            for (mid, key), mark in marks.items()
        ])
        stats["achievements"] += len(marks)
        stats["marked"] += sum(1 for m in marks.values() if m == "L")
        print(f"  {table}: {len(rows)} rows -> {len(marks)} achievements ({role})")

    current_table[0] = "SCORE"
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

    current_table[0] = "PARSE"
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

    # --- the guild's reference material: CP builds, links, setups -------------
    guides = 0
    for table in ("CP", "LINK", "SETUP", "CRAFT", "FARMING"):
        columns, rows = read_table(old, table)
        if not columns:
            continue
        statements = []
        for row in rows:
            command = str(row[0] or "").strip()
            body = str(row[1] or "")
            if not command or not body.strip():
                continue
            # the old bot stored newlines as a literal backslash-n and un-escaped
            # them at send time; do it once, here.
            body = body.replace("\\n", "\n").replace("\r\n", "\n")
            statements.append((
                "INSERT OR REPLACE INTO guides(category, topic, body) VALUES(?,?,?)",
                (table.lower(), command, body)))
        await db.run_many(statements)
        guides += len(statements)
        print(f"  {table}: {len(statements)} guide entries")

    stats["members"] = await db.val("SELECT COUNT(*) FROM members", (), 0)
    await db.close()
    old.close()

    print(f"\nDone. {stats['members']} members, {stats['achievements']} achievements "
          f"({stats['marked']} carrying the 'L' marker), {stats['scores']} scores, "
          f"{stats['parses']} parses, {guides} guide entries.")
    merged = len({tag for (_, tag), n in seen_rows.items() if n > 1})
    if merged:
        print(f"\n{merged} gamertag(s) appeared on more than one row of the same sheet and "
              f"were merged into one member each.")
        if stats["disagreements"]:
            print(f"  {stats['disagreements']} of those cells disagreed between rows "
                  f"(one said X, another L) - the first was kept.")
    if stats["unreadable"]:
        print("\nCells that weren't X, L or blank (skipped, never guessed at):")
        for value, n in stats["unreadable"].most_common(10):
            print(f"  {value!r}: {n}")
    if stats["unknown_columns"]:
        print("\nColumns I did not recognise (nothing was lost, they were just skipped):")
        print("  " + ", ".join(sorted(stats["unknown_columns"])))
        print("Add them with /achievement new, then re-run this, or fix them with /import.")
    print("\nThe old file stored Discord display names, not account ids, so nobody is linked "
          "yet.\nRun /member match in Discord - it looks each saved name up against your "
          "server\nand links the ones that match exactly.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python tools/migrate_legacy.py <old database.db> [new db path]")
    asyncio.run(migrate(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "data/unify.sqlite3"))
