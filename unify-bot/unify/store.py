"""Domain logic: members, achievement grants with prerequisite chains, scores,
parses, leaderboards and the audit trail. Cogs stay thin by calling into here."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from .config import POINTS
from .db import Database


# --------------------------------------------------------------------------- members
@dataclass
class Member:
    id: int
    discord_id: int | None
    gamertag: str
    active: int

    @property
    def mention(self) -> str:
        return f"<@{self.discord_id}>" if self.discord_id else self.gamertag


def _member(row) -> Member | None:
    return Member(row["id"], row["discord_id"], row["gamertag"], row["active"]) if row else None


async def get_member(db: Database, member_id: int) -> Member | None:
    return _member(await db.one("SELECT * FROM members WHERE id = ?", (member_id,)))


async def find_member(db: Database, query: str | int) -> Member | None:
    """Accept a discord id, a <@mention>, or a gamertag - whatever the user typed."""
    if isinstance(query, int):
        return _member(await db.one("SELECT * FROM members WHERE discord_id = ?", (query,)))
    q = str(query).strip()
    digits = q.strip("<@!>&")
    if digits.isdigit():
        row = await db.one("SELECT * FROM members WHERE discord_id = ?", (int(digits),))
        if row:
            return _member(row)
        row = await db.one("SELECT * FROM members WHERE id = ?", (int(digits),))
        if row:
            return _member(row)
    return _member(await db.one("SELECT * FROM members WHERE gamertag = ?", (q,)))


async def search_members(db: Database, query: str, limit: int = 25) -> list[Member]:
    like = f"%{query.strip()}%"
    rows = await db.all(
        "SELECT * FROM members WHERE active = 1 AND gamertag LIKE ? "
        "ORDER BY gamertag LIMIT ?",
        (like, limit),
    )
    return [_member(r) for r in rows]


async def create_member(db: Database, gamertag: str, discord_id: int | None) -> Member:
    cur = await db.run(
        "INSERT INTO members(gamertag, discord_id) VALUES(?, ?)", (gamertag.strip(), discord_id)
    )
    return await get_member(db, cur.lastrowid)


async def upsert_member(db: Database, gamertag: str, discord_id: int | None) -> tuple[Member, bool]:
    """Returns (member, created). Links a discord id onto an existing gamertag."""
    existing = await find_member(db, gamertag)
    if existing is None and discord_id:
        existing = await find_member(db, discord_id)
    if existing:
        updates = []
        if discord_id and existing.discord_id != discord_id:
            updates.append(("UPDATE members SET discord_id = ? WHERE id = ?", (discord_id, existing.id)))
        if gamertag and existing.gamertag.lower() != gamertag.strip().lower():
            updates.append(("UPDATE members SET gamertag = ? WHERE id = ?", (gamertag.strip(), existing.id)))
        if updates:
            await db.run_many(updates)
            existing = await get_member(db, existing.id)
        return existing, False
    return await create_member(db, gamertag, discord_id), True


# ------------------------------------------------------------------- achievement graph
async def catalog(db: Database) -> dict[str, dict[str, Any]]:
    rows = await db.all("SELECT * FROM achievements")
    return {r["key"]: dict(r) for r in rows}


async def expand_prerequisites(db: Database, keys: Sequence[str]) -> list[str]:
    """vsshm implies vssice/vssfire/vssnavi/vss. Walk the chain upward."""
    cat = await catalog(db)
    out: list[str] = []
    stack = [k for k in keys if k in cat]
    seen: set[str] = set()
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        stack.extend(p for p in cat[key]["requires"].split() if p in cat)
    return sorted(out, key=lambda k: (cat[k]["trial_key"], cat[k]["sort"]))


async def expand_dependents(db: Database, keys: Sequence[str]) -> list[str]:
    """Removing vss must also remove everything that required it."""
    cat = await catalog(db)
    out: set[str] = set()
    stack = list(keys)
    while stack:
        key = stack.pop()
        if key in out:
            continue
        out.add(key)
        stack.extend(k for k, a in cat.items() if key in a["requires"].split() and k not in out)
    return sorted(out, key=lambda k: (cat[k]["trial_key"], cat[k]["sort"]) if k in cat else ("", 0))


async def owned(db: Database, member_id: int, role: str) -> set[str]:
    rows = await db.all(
        "SELECT achievement_key FROM member_achievements WHERE member_id = ? AND role = ?",
        (member_id, role),
    )
    return {r[0] for r in rows}


async def grant(
    db: Database, member: Member, role: str, keys: Sequence[str], actor_id: int, actor_name: str
) -> list[str]:
    """Grants keys plus their prerequisites. Returns only what was actually new."""
    wanted = await expand_prerequisites(db, keys)
    have = await owned(db, member.id, role)
    added = [k for k in wanted if k not in have]
    if not added:
        return []
    await db.run_many(
        [
            (
                "INSERT OR IGNORE INTO member_achievements"
                "(member_id, role, achievement_key, granted_by) VALUES(?,?,?,?)",
                (member.id, role, k, actor_id),
            )
            for k in added
        ]
    )
    await log_action(
        db, actor_id, actor_name, "grant",
        f"{member.gamertag} [{role}] +{', '.join(added)}",
        undo=[
            ("DELETE FROM member_achievements WHERE member_id=? AND role=? AND achievement_key=?",
             (member.id, role, k))
            for k in added
        ],
    )
    return added


async def revoke(
    db: Database, member: Member, role: str, keys: Sequence[str], actor_id: int, actor_name: str
) -> list[str]:
    """Revokes keys plus anything that depended on them. Returns what was removed."""
    targets = await expand_dependents(db, keys)
    have = await owned(db, member.id, role)
    removed = [k for k in targets if k in have]
    if not removed:
        return []
    await db.run_many(
        [
            ("DELETE FROM member_achievements WHERE member_id=? AND role=? AND achievement_key=?",
             (member.id, role, k))
            for k in removed
        ]
    )
    await log_action(
        db, actor_id, actor_name, "revoke",
        f"{member.gamertag} [{role}] -{', '.join(removed)}",
        undo=[
            ("INSERT OR IGNORE INTO member_achievements(member_id, role, achievement_key, granted_by)"
             " VALUES(?,?,?,?)", (member.id, role, k, actor_id))
            for k in removed
        ],
    )
    return removed


async def progress(db: Database, member_id: int, role: str) -> list[dict[str, Any]]:
    """One row per trial: total achievements, how many this member holds."""
    rows = await db.all(
        """
        SELECT t.key, t.name, t.short, t.emoji, t.sort,
               COUNT(a.key) AS total,
               COUNT(ma.achievement_key) AS done
        FROM trials t
        JOIN achievements a ON a.trial_key = t.key
        LEFT JOIN member_achievements ma
               ON ma.achievement_key = a.key AND ma.member_id = ? AND ma.role = ?
        GROUP BY t.key
        ORDER BY t.sort, t.name
        """,
        (member_id, role),
    )
    return [dict(r) for r in rows]


async def trial_detail(db: Database, member_id: int, role: str, trial_key: str) -> list[dict]:
    rows = await db.all(
        """
        SELECT a.key, a.name, a.kind, ma.granted_at IS NOT NULL AS have, ma.granted_at
        FROM achievements a
        LEFT JOIN member_achievements ma
               ON ma.achievement_key = a.key AND ma.member_id = ? AND ma.role = ?
        WHERE a.trial_key = ?
        ORDER BY a.sort, a.name
        """,
        (member_id, role, trial_key),
    )
    return [dict(r) for r in rows]


async def titles(db: Database, member_id: int) -> list[str]:
    rows = await db.all(
        """
        SELECT DISTINCT a.name FROM member_achievements ma
        JOIN achievements a ON a.key = ma.achievement_key
        WHERE ma.member_id = ? AND a.kind = 'title'
        ORDER BY a.sort
        """,
        (member_id,),
    )
    return [r[0] for r in rows]


async def points(db: Database, member_id: int) -> int:
    rows = await db.all(
        "SELECT a.kind, COUNT(*) FROM member_achievements ma "
        "JOIN achievements a ON a.key = ma.achievement_key "
        "WHERE ma.member_id = ? GROUP BY a.kind",
        (member_id,),
    )
    return sum(POINTS.get(kind, 1) * n for kind, n in rows)


# --------------------------------------------------------------------------- scores
async def set_score(db, member: Member, trial_key: str, score: int, actor_id, actor_name) -> None:
    before = await db.val("SELECT score FROM scores WHERE member_id=? AND trial_key=?",
                          (member.id, trial_key))
    await db.run(
        "INSERT INTO scores(member_id, trial_key, score, recorded_by) VALUES(?,?,?,?) "
        "ON CONFLICT(member_id, trial_key) DO UPDATE SET score=excluded.score, "
        "recorded_at=datetime('now'), recorded_by=excluded.recorded_by",
        (member.id, trial_key, score, actor_id),
    )
    undo = ([("UPDATE scores SET score=? WHERE member_id=? AND trial_key=?",
              (before, member.id, trial_key))] if before is not None else
            [("DELETE FROM scores WHERE member_id=? AND trial_key=?", (member.id, trial_key))])
    await log_action(db, actor_id, actor_name, "score",
                     f"{member.gamertag} {trial_key} = {score:,}", undo=undo)


async def set_parse(db, member: Member, label: str, dps: int, actor_id, actor_name) -> None:
    before = await db.val("SELECT dps FROM parses WHERE member_id=? AND label=?",
                          (member.id, label))
    await db.run(
        "INSERT INTO parses(member_id, label, dps, recorded_by) VALUES(?,?,?,?) "
        "ON CONFLICT(member_id, label) DO UPDATE SET dps=excluded.dps, "
        "recorded_at=datetime('now'), recorded_by=excluded.recorded_by",
        (member.id, label, dps, actor_id),
    )
    undo = ([("UPDATE parses SET dps=? WHERE member_id=? AND label=?", (before, member.id, label))]
            if before is not None else
            [("DELETE FROM parses WHERE member_id=? AND label=?", (member.id, label))])
    await log_action(db, actor_id, actor_name, "parse",
                     f"{member.gamertag} {label} = {dps:,}", undo=undo)


# --------------------------------------------------------------------------- audit
async def log_action(db: Database, actor_id: int, actor_name: str, action: str,
                     summary: str, undo: list[tuple[str, Sequence[Any]]] | None = None) -> int:
    cur = await db.run(
        "INSERT INTO audit_log(actor_id, actor_name, action, summary, undo) VALUES(?,?,?,?,?)",
        (actor_id, actor_name, action, summary,
         json.dumps([[s, list(a)] for s, a in (undo or [])])),
    )
    return cur.lastrowid


async def undo_action(db: Database, entry_id: int) -> str | None:
    row = await db.one("SELECT * FROM audit_log WHERE id = ? AND undone = 0", (entry_id,))
    if row is None:
        return None
    statements = [(s, a) for s, a in json.loads(row["undo"] or "[]")]
    if not statements:
        return None
    await db.run_many(statements)
    await db.run("UPDATE audit_log SET undone = 1 WHERE id = ?", (entry_id,))
    return row["summary"]
