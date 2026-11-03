"""Domain logic: members, achievement grants with prerequisite chains, scores,
parses, leaderboards and the audit trail. Cogs stay thin by calling into here."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .config import ALL_ROLES, POINTS
from .db import Database


# --------------------------------------------------------------------------- members
@dataclass
class Member:
    id: int
    discord_id: int | None
    gamertag: str
    active: int
    legacy_name: str = ""

    @property
    def mention(self) -> str:
        return f"<@{self.discord_id}>" if self.discord_id else self.gamertag


def _member(row) -> Member | None:
    if not row:
        return None
    legacy = row["legacy_name"] if "legacy_name" in row.keys() else ""
    return Member(row["id"], row["discord_id"], row["gamertag"], row["active"], legacy or "")


async def get_member(db: Database, member_id: int) -> Member | None:
    return _member(await db.one("SELECT * FROM members WHERE id = ?", (member_id,)))


async def find_member(db: Database, query: str | int) -> Member | None:
    """Accept a discord id, a <@mention>, or a gamertag - whatever the user typed.

    Only a 15-25 digit run counts as a Discord id; anything else is treated as a
    gamertag, so a member who calls themselves "3" can never resolve to somebody
    else's database row."""
    if isinstance(query, int):
        return _member(await db.one("SELECT * FROM members WHERE discord_id = ?", (query,)))
    q = str(query).strip()
    digits = q.strip("<@!>&")
    if digits.isdigit() and 15 <= len(digits) <= 25:
        row = await db.one("SELECT * FROM members WHERE discord_id = ?", (int(digits),))
        if row:
            return _member(row)
    return _member(await db.one("SELECT * FROM members WHERE gamertag = ?", (q,)))


def strip_discriminator(name: str) -> str:
    """"A Sly Man#9733" and "A Sly Man" are the same person - Discord retired
    the #1234 suffix, but the old spreadsheet still carries it."""
    return re.sub(r"#\d{4}$", "", str(name or "").strip()).strip().lower()


@dataclass
class Lookup:
    member: Member | None
    candidates: list[Member] = field(default_factory=list)   # set when ambiguous


async def same_gamertag(db: Database, gamertag: str) -> list[Member]:
    """Every record whose gamertag is this one, ignoring case and a trailing dot.

    96% of the roster carries a trailing dot by guild convention, and people leave
    it off when they type their own gamertag. Claims and /member add use this so
    `BUDMAN008` finds `BUDMAN008.` instead of creating a second, empty record. Only
    the dot is forgiven; no other near miss ever matches."""
    core = gamertag.strip().lower().rstrip(".").strip()
    if not core:
        return []
    rows = await db.all(
        "SELECT * FROM members WHERE RTRIM(LOWER(gamertag), '.') = ? ORDER BY id", (core,))
    return [_member(r) for r in rows]


async def lookup_member(db: Database, query: str | int, guild=None) -> Lookup:
    """For a name a person typed into a command: gamertag, Discord mention or id,
    the Discord name from the old spreadsheet, or their current Discord name.

    Deliberately NOT used by identity decisions - roster claims and imports only
    forgive a trailing dot (same_gamertag), and the channel reader stays on exact
    find_member - because a loose match there could hand one person's record to
    another. Here a near miss only shows the wrong card, and two people answering
    to one name are refused rather than guessed."""
    exact = await find_member(db, query)
    if exact is not None or isinstance(query, int):
        return Lookup(exact)
    wanted = strip_discriminator(str(query))
    if not wanted:
        return Lookup(None)

    found: dict[int, Member] = {}
    # Two records differing only by the trailing dot are refused below.
    for member in await same_gamertag(db, wanted):
        found[member.id] = member

    for row in await db.all("SELECT * FROM members WHERE legacy_name <> ''"):
        if strip_discriminator(row["legacy_name"]) == wanted:
            found[row["id"]] = _member(row)

    if guild is not None:
        for person in getattr(guild, "members", []):
            names = {person.name, getattr(person, "global_name", None),
                     getattr(person, "display_name", None)}
            if wanted in {strip_discriminator(n) for n in names if n}:
                member = await find_member(db, person.id)
                if member is not None:
                    found[member.id] = member

    if len(found) == 1:
        return Lookup(next(iter(found.values())))
    return Lookup(None, sorted(found.values(), key=lambda m: m.gamertag.lower()))


async def search_members(db: Database, query: str, limit: int = 25) -> list[Member]:
    like = f"%{query.strip()}%"
    rows = await db.all(
        "SELECT * FROM members WHERE active = 1 AND (gamertag LIKE ? OR legacy_name LIKE ?) "
        "ORDER BY gamertag LIMIT ?",
        (like, like, limit),
    )
    return [_member(r) for r in rows]


async def create_member(db: Database, gamertag: str, discord_id: int | None) -> Member:
    cur = await db.run(
        "INSERT INTO members(gamertag, discord_id) VALUES(?, ?)", (gamertag.strip(), discord_id)
    )
    return await get_member(db, cur.lastrowid)


@dataclass
class Upsert:
    member: Member
    created: bool
    conflict: str = ""      # empty when nothing surprising happened


async def upsert_member(db: Database, gamertag: str, discord_id: int | None) -> Upsert:
    """Find or create a member by gamertag, linking a Discord account when it is free.

    Deliberately never renames anybody and never moves a Discord link off an
    existing member: a spreadsheet with the same Discord id against two different
    gamertags used to silently merge those two people into one record and hand one
    of them the other's achievements. Now both survive and the caller is told."""
    gamertag = gamertag.strip()
    by_tag = await find_member(db, gamertag)
    if by_tag is None:
        loose = await same_gamertag(db, gamertag)      # `BUDMAN008` is `BUDMAN008.`
        if len(loose) == 1:
            by_tag = loose[0]
    owner = await find_member(db, discord_id) if discord_id else None

    if by_tag:
        if discord_id and by_tag.discord_id == discord_id:
            return Upsert(by_tag, False)
        if discord_id and by_tag.discord_id:
            return Upsert(by_tag, False,
                          f"{gamertag} is already linked to a different Discord account")
        if discord_id and owner and owner.id != by_tag.id:
            return Upsert(by_tag, False,
                          f"that Discord account is already {owner.gamertag}")
        if discord_id:
            await db.run("UPDATE members SET discord_id = ? WHERE id = ?", (discord_id, by_tag.id))
            return Upsert(await get_member(db, by_tag.id), False)
        return Upsert(by_tag, False)

    if owner:
        # New gamertag, but this Discord account already belongs to somebody. Keep
        # both records rather than renaming one of them out of existence.
        return Upsert(await create_member(db, gamertag, None), True,
                      f"{gamertag} added without a Discord link - "
                      f"that account is already {owner.gamertag}")
    return Upsert(await create_member(db, gamertag, discord_id), True)


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
    db: Database, member: Member, role: str, keys: Sequence[str], actor_id: int, actor_name: str,
    mark: str = "X",     # 'L' marks a legacy, pre-account-wide record
    evidence: str = "",  # link to the screenshot it was recorded from
) -> list[str]:
    """Grants keys plus their prerequisites. Returns only what was actually new."""
    if role not in ALL_ROLES:
        # Every path that writes achievements funnels through here, so this is the
        # last line of defence against a record no profile could ever show.
        raise ValueError(f"refusing to record achievements under role {role!r}")
    wanted = await expand_prerequisites(db, keys)
    have = await owned(db, member.id, role)
    added = [k for k in wanted if k not in have]
    if not added:
        return []
    await db.run_many(
        [
            (
                "INSERT OR IGNORE INTO member_achievements"
                "(member_id, role, achievement_key, mark, granted_by) VALUES(?,?,?,?,?)",
                (member.id, role, k, mark, actor_id),
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
        evidence=evidence,
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
        SELECT a.key, a.name, a.kind, ma.granted_at IS NOT NULL AS have,
               COALESCE(ma.mark, '') AS mark, ma.granted_at
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
async def set_score(db, member: Member, trial_key: str, score: int, actor_id, actor_name,
                    evidence: str = "") -> None:
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
                     f"{member.gamertag} {trial_key} = {score:,}", undo=undo, evidence=evidence)


async def raise_score(db, member: Member, trial_key: str, score: int,
                      actor_id, actor_name, evidence: str = "") -> bool:
    """Record a run's score for one member, but only if it beats what they have.

    Every run is posted, good or bad, so a straight overwrite would replace a
    member's best with whatever they ran last night. An officer correcting a
    number uses /record score, which still sets it exactly."""
    before = await db.val("SELECT score FROM scores WHERE member_id=? AND trial_key=?",
                          (member.id, trial_key))
    if before is not None and before >= score:
        return False
    await set_score(db, member, trial_key, score, actor_id, actor_name, evidence)
    return True


async def set_parse(db, member: Member, label: str, dps: int, actor_id, actor_name,
                    evidence: str = "") -> None:
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
                     f"{member.gamertag} {label} = {dps:,}", undo=undo, evidence=evidence)


# --------------------------------------------------------------------------- merging
@dataclass
class Merge:
    """Everything folding one record into another would do, worked out first so an
    officer sees it before it happens, with the exact steps to put it all back."""
    duplicate: Member
    keep: Member
    clears: int = 0          # clears that move over
    already: int = 0         # clears the kept record already had
    upgraded: int = 0        # legacy marks on the kept record a regular clear replaces
    scores: int = 0          # scores that were better on the duplicate
    parses: int = 0          # parses that were better on the duplicate
    link: bool = False       # the duplicate's Discord link moves over
    problem: str = ""        # why it can't be done
    statements: list = field(default_factory=list)
    undo: list = field(default_factory=list)


def _insert(table: str, row, **override) -> tuple[str, list]:
    columns = list(row.keys())
    return (f"INSERT INTO {table}({', '.join(columns)}) VALUES({', '.join('?' * len(columns))})",
            [override.get(c, row[c]) for c in columns])


async def plan_merge(db: Database, duplicate: Member, keep: Member) -> Merge:
    """Fold `duplicate` into `keep`: every clear (a regular clear beats a legacy
    mark), the better score per trial and parse per label, and the Discord link if
    only the duplicate has one. Then the duplicate is deleted."""
    plan = Merge(duplicate, keep)
    if duplicate.id == keep.id:
        plan.problem = "that's the same record twice"
        return plan
    if duplicate.discord_id and keep.discord_id and duplicate.discord_id != keep.discord_id:
        plan.problem = (f"**{duplicate.gamertag}** and **{keep.gamertag}** are linked to two "
                        "different Discord accounts, so they may be two people. Unlink one "
                        "with `/member link` first if they really are the same person.")
        return plan

    dup_row = await db.one("SELECT * FROM members WHERE id = ?", (duplicate.id,))
    keep_row = await db.one("SELECT * FROM members WHERE id = ?", (keep.id,))
    moves, keep_rows_undo, restore = [], [], [_insert("members", dup_row)]

    have = {(r["role"], r["achievement_key"]): r["mark"] for r in await db.all(
        "SELECT role, achievement_key, mark FROM member_achievements WHERE member_id = ?",
        (keep.id,))}
    for r in await db.all("SELECT * FROM member_achievements WHERE member_id = ?",
                          (duplicate.id,)):
        where = (keep.id, r["role"], r["achievement_key"])
        mark = have.get((r["role"], r["achievement_key"]))
        if mark is None:
            moves.append(_insert("member_achievements", r, member_id=keep.id))
            keep_rows_undo.append(("DELETE FROM member_achievements WHERE member_id = ? "
                                   "AND role = ? AND achievement_key = ?", where))
            plan.clears += 1
        elif mark == "L" and r["mark"] == "X":
            moves.append(("UPDATE member_achievements SET mark = 'X' WHERE member_id = ? "
                          "AND role = ? AND achievement_key = ?", where))
            keep_rows_undo.append(("UPDATE member_achievements SET mark = 'L' WHERE "
                                   "member_id = ? AND role = ? AND achievement_key = ?", where))
            plan.upgraded += 1
        else:
            plan.already += 1
        restore.append(_insert("member_achievements", r))

    better = {}
    for table, key, value in (("scores", "trial_key", "score"), ("parses", "label", "dps")):
        mine = {r[key]: r for r in await db.all(f"SELECT * FROM {table} WHERE member_id = ?",
                                                (keep.id,))}
        better[table] = 0
        for r in await db.all(f"SELECT * FROM {table} WHERE member_id = ?", (duplicate.id,)):
            restore.append(_insert(table, r))
            old = mine.get(r[key])
            if old is None:
                moves.append(_insert(table, r, member_id=keep.id))
                keep_rows_undo.append((f"DELETE FROM {table} WHERE member_id = ? AND {key} = ?",
                                       (keep.id, r[key])))
                better[table] += 1
            elif r[value] > old[value]:
                moves.append((f"UPDATE {table} SET {value} = ?, recorded_at = ?, recorded_by = ? "
                              f"WHERE member_id = ? AND {key} = ?",
                              (r[value], r["recorded_at"], r["recorded_by"], keep.id, r[key])))
                keep_rows_undo.append((f"UPDATE {table} SET {value} = ?, recorded_at = ?, "
                                       f"recorded_by = ? WHERE member_id = ? AND {key} = ?",
                                       (old[value], old["recorded_at"], old["recorded_by"],
                                        keep.id, r[key])))
                better[table] += 1
    plan.scores, plan.parses = better["scores"], better["parses"]

    # Decided requests are history: they follow the person rather than vanish.
    history = [r[0] for r in await db.all("SELECT id FROM requests WHERE member_id = ?",
                                          (duplicate.id,))]
    moves += [("UPDATE requests SET member_id = ? WHERE id = ?", (keep.id, i)) for i in history]
    requests_back = [("UPDATE requests SET member_id = ? WHERE id = ?", (duplicate.id, i))
                     for i in history]

    keep_member_undo = []
    after = [("DELETE FROM members WHERE id = ?", (duplicate.id,))]
    if duplicate.discord_id and not keep.discord_id:
        plan.link = True
        after.append(("UPDATE members SET discord_id = ? WHERE id = ?", (duplicate.discord_id, keep.id)))
        keep_member_undo.append(("UPDATE members SET discord_id = NULL WHERE id = ?", (keep.id,)))
    if dup_row["legacy_name"] and not keep_row["legacy_name"]:
        after.append(("UPDATE members SET legacy_name = ? WHERE id = ?",
                      (dup_row["legacy_name"], keep.id)))
        keep_member_undo.append(("UPDATE members SET legacy_name = '' WHERE id = ?", (keep.id,)))
    if dup_row["active"] and not keep_row["active"]:
        after.append(("UPDATE members SET active = 1, left_server = 0 WHERE id = ?", (keep.id,)))
        keep_member_undo.append(("UPDATE members SET active = ?, left_server = ? WHERE id = ?",
                                 (keep_row["active"], keep_row["left_server"], keep.id)))

    plan.statements = moves + after
    # Undone in the reverse order: free the Discord link before the duplicate is
    # recreated with its own id, so older audit entries pointing at it still work.
    plan.undo = keep_member_undo + restore + requests_back + keep_rows_undo
    return plan


async def apply_merge(db: Database, plan: Merge, actor_id: int, actor_name: str) -> int:
    await db.run_many(plan.statements)
    return await log_action(db, actor_id, actor_name, "member merge",
                            f"{plan.duplicate.gamertag} into {plan.keep.gamertag}", undo=plan.undo)


# --------------------------------------------------------------------------- audit
async def log_action(db: Database, actor_id: int, actor_name: str, action: str,
                     summary: str, undo: list[tuple[str, Sequence[Any]]] | None = None,
                     evidence: str = "") -> int:
    cur = await db.run(
        "INSERT INTO audit_log(actor_id, actor_name, action, summary, undo, evidence) "
        "VALUES(?,?,?,?,?,?)",
        (actor_id, actor_name, action, summary,
         json.dumps([[s, list(a)] for s, a in (undo or [])]), evidence),
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
