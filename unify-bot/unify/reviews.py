"""The review queue: the one place anything a member asked for waits for an officer.

The rule the whole design rests on: **a member can create a request, but only an
officer or a trusted recorder can cause a row to be written.** Nothing in here
writes guild data as a side effect of somebody asking."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import store
from .db import Database

KINDS = ("run", "achievements", "register", "score", "parse", "check", "report")
KIND_LABEL = {
    "run": "Run",
    "achievements": "Achievements",
    "register": "Roster claim",
    "score": "Trial score",
    "parse": "Parse",
    "check": "Spot check",
    "report": "Record report",
}
KIND_EMOJI = {"run": "⚔️", "achievements": "🏅", "register": "👤", "score": "🏆", "parse": "📈",
              "check": "🔍", "report": "🚩"}
ROLES_FOR_CLEARS = ("tank", "healer", "dps")


# --------------------------------------------------------------------- routing
@dataclass(frozen=True)
class Routing:
    auto: bool
    note: str          # why it needs a human, empty when it doesn't


def route(
    *,
    submitter_id: int,
    subject_discord_id: int | None,
    submitter_trusted: bool,
    member_known: bool,
    all_roles_mapped: bool,
    removes: bool,
) -> Routing:
    """Decide whether a submission applies immediately or waits for an officer.

    Deliberately conservative, and the order matters. The self-submission check
    sits *above* the trust check so that a trusted recorder still cannot record
    their own clear - that is the one hole a trusted-role scheme would otherwise
    leave wide open."""
    if not member_known:
        return Routing(False, "not on the roster yet")
    if not all_roles_mapped:
        return Routing(False, "mentions a role I don't recognise")
    if removes:
        return Routing(False, "takes something away - never automatic")
    if subject_discord_id is not None and submitter_id == subject_discord_id:
        return Routing(False, "you can't record your own clear")
    if submitter_trusted:
        return Routing(True, "")
    return Routing(False, "submitted by someone who isn't a recorder")


def decide(
    mode: str,
    *,
    is_recorder: bool,
    has_role: bool,
    submitter_id: int,
    subject_discord_id: int | None,
    member_known: bool,
    all_roles_mapped: bool,
    removes: bool = False,
) -> Routing:
    """The whole call: submission_mode plus the routing rules.

    `review` queues everything. `trusted` applies only for recorders. There is
    deliberately no mode where a member's own post applies."""
    if not has_role:
        return Routing(False, "no role given - pick one when approving")
    if mode == "review":
        return Routing(False, "every submission is reviewed here")
    return route(
        submitter_id=submitter_id,
        subject_discord_id=subject_discord_id,
        submitter_trusted=is_recorder,
        member_known=member_known,
        all_roles_mapped=all_roles_mapped,
        removes=removes,
    )


# --------------------------------------------------------------------- storage
async def open_request(
    db: Database,
    kind: str,
    *,
    requester_id: int,
    member_id: int | None = None,
    subject: str = "",
    role: str = "",
    payload: dict[str, Any] | None = None,
    proof_url: str = "",
    source_url: str = "",
    note: str = "",
) -> int:
    cur = await db.run(
        "INSERT INTO requests(kind, requester_id, member_id, subject, role, payload, "
        "proof_url, source_url, note) VALUES(?,?,?,?,?,?,?,?,?)",
        (kind, requester_id, member_id, subject, role,
         json.dumps(payload or {}), proof_url, source_url, note),
    )
    return cur.lastrowid


async def attach_card(db: Database, request_id: int, message_id: int, channel_id: int) -> None:
    await db.run("UPDATE requests SET message_id = ?, channel_id = ? WHERE id = ?",
                 (message_id, channel_id, request_id))


async def get(db: Database, request_id: int):
    return await db.one("SELECT * FROM requests WHERE id = ?", (request_id,))


async def by_message(db: Database, message_id: int):
    return await db.one(
        "SELECT * FROM requests WHERE message_id = ? AND status = 'pending'", (message_id,))


async def pending(db: Database, kind: str = "", limit: int = 10, offset: int = 0):
    clause = " AND kind = ?" if kind else ""
    args: list = [kind] if kind else []
    return await db.all(
        f"SELECT * FROM requests WHERE status = 'pending'{clause} "
        "ORDER BY id LIMIT ? OFFSET ?", (*args, limit, offset))


async def pending_count(db: Database, kind: str = "") -> int:
    clause = " AND kind = ?" if kind else ""
    args: tuple = (kind,) if kind else ()
    return await db.val(
        f"SELECT COUNT(*) FROM requests WHERE status = 'pending'{clause}", args, 0)


async def counts_by_kind(db: Database) -> dict[str, int]:
    rows = await db.all(
        "SELECT kind, COUNT(*) FROM requests WHERE status = 'pending' GROUP BY kind")
    return {r[0]: r[1] for r in rows}


async def close(db: Database, request_id: int, status: str, actor_id: int) -> None:
    await db.run(
        "UPDATE requests SET status = ?, decided_by = ?, decided_at = datetime('now') "
        "WHERE id = ? AND status = 'pending'",
        (status, actor_id, request_id))


# ------------------------------------------------------------------ duplicates
async def already_recorded(db: Database, member_id: int, role: str,
                           keys: Sequence[str]) -> bool:
    """True when the member already holds everything being submitted."""
    if not keys:
        return False
    wanted = set(await store.expand_prerequisites(db, keys))
    return wanted <= await store.owned(db, member_id, role)


async def duplicate_request(db: Database, kind: str, member_id: int | None,
                            role: str, payload: dict) -> int | None:
    """A waiting request that matches on every field given. Returns its id, or None.

    Only the fields passed are compared, so callers can leave out the parts that
    legitimately differ between two copies of the same claim, such as the raw
    text of the post."""
    rows = await db.all(
        "SELECT id, payload FROM requests WHERE status = 'pending' AND kind = ? "
        "AND IFNULL(member_id, -1) = IFNULL(?, -1) AND role = ?",
        (kind, member_id, role))
    target = json.dumps(payload, sort_keys=True)
    for row in rows:
        try:
            stored = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        if json.dumps({k: stored.get(k) for k in payload}, sort_keys=True) == target:
            return row["id"]
    return None


# ----------------------------------------------------------------- applying it
@dataclass
class Applied:
    ok: bool
    summary: str
    member: store.Member | None = None
    run: "RunResult | None" = None


async def apply(db: Database, row, actor_id: int, actor_name: str,
                evidence: str = "") -> Applied:
    """Carry out an approved request. Re-checks everything: the world may have
    moved since it was raised. `evidence` links what the officer looked at."""
    kind = row["kind"]
    payload = json.loads(row["payload"] or "{}")

    if kind == "register":
        return await _apply_register(db, row, payload, actor_id, actor_name, evidence)
    if kind == "run":
        return await _apply_run(db, payload, actor_id, actor_name, evidence)
    if kind == "check":
        return Applied(True, "checked, and it looks right")
    if kind == "report":
        return Applied(True, "marked as dealt with")

    member = await store.get_member(db, row["member_id"]) if row["member_id"] else None
    if member is None:
        return Applied(False, "that member is no longer on the roster")

    if kind == "achievements":
        if row["role"] not in ROLES_FOR_CLEARS:
            return Applied(False, "no role is set yet - pick Tank, Healer or DPS on the card")
        keys = payload.get("keys", [])
        added = await store.grant(db, member, row["role"], keys, actor_id, actor_name,
                                  evidence=evidence)
        return Applied(True,
                       ", ".join(added) if added else "already had all of these",
                       member)

    if kind == "score":
        trial, score = payload.get("trial", ""), int(payload.get("score", 0))
        if not await db.one("SELECT 1 FROM trials WHERE key = ?", (trial,)):
            return Applied(False, f"`{trial}` is no longer a trial I know")
        await store.set_score(db, member, trial, score, actor_id, actor_name, evidence)
        return Applied(True, f"{trial} = {score:,}", member)

    if kind == "parse":
        label, dps = payload.get("label", ""), int(payload.get("dps", 0))
        await store.set_parse(db, member, label, dps, actor_id, actor_name, evidence)
        return Applied(True, f"{label} = {dps:,}", member)

    return Applied(False, f"I don't know how to apply a '{kind}' request")


@dataclass
class RunResult:
    recorded: list[tuple[store.Member, str, list[str]]] = field(default_factory=list)
    no_role: list[store.Member] = field(default_factory=list)
    gone: int = 0
    score_raised: list[store.Member] = field(default_factory=list)
    score_kept: list[store.Member] = field(default_factory=list)


async def apply_run_parts(db: Database, payload: dict, actor_id: int,
                          actor_name: str, evidence: str = "") -> RunResult:
    """The shared work of recording a run: clears per person under their own role,
    and the run's score for everyone in it, best score winning."""
    out = RunResult()
    keys = payload.get("keys", [])
    for member_id, role in payload.get("members", []):
        member = await store.get_member(db, member_id)
        if member is None:
            out.gone += 1
            continue
        if role not in ROLES_FOR_CLEARS:
            out.no_role.append(member)
            continue
        if keys:
            added = await store.grant(db, member, role, keys, actor_id, actor_name,
                                      evidence=evidence)
            out.recorded.append((member, role, added))

    score, trial = payload.get("score"), payload.get("trial", "")
    if score and trial:
        for member_id in payload.get("score_members", []):
            member = await store.get_member(db, member_id)
            if member is None:
                continue
            raised = await store.raise_score(db, member, trial, int(score), actor_id, actor_name,
                                             evidence)
            (out.score_raised if raised else out.score_kept).append(member)
    return out


async def _apply_run(db: Database, payload: dict, actor_id: int, actor_name: str,
                     evidence: str = "") -> Applied:
    if payload.get("unmapped"):
        return Applied(False, "this post is waiting on a role mapping - map the role "
                              "and it comes back automatically")
    if payload.get("score") and payload.get("trial"):
        if not await db.one("SELECT 1 FROM trials WHERE key = ?", (payload["trial"],)):
            return Applied(False, f"`{payload['trial']}` is no longer a trial I know")
    if payload.get("members") and all(role not in ROLES_FOR_CLEARS
                                      for _, role in payload["members"]):
        return Applied(False, "nobody on this run has a role yet - pick Tank, Healer or "
                              "DPS on the card first")

    result = await apply_run_parts(db, payload, actor_id, actor_name, evidence)
    bits = []
    if result.recorded:
        new = sum(1 for _, _, added in result.recorded if added)
        bits.append(f"clears for {len(result.recorded)}"
                    + (f" ({len(result.recorded) - new} already had them)"
                       if new < len(result.recorded) else ""))
    if result.score_raised or result.score_kept:
        bits.append(f"{payload['trial']} score {int(payload['score']):,} for "
                    f"{len(result.score_raised) + len(result.score_kept)}"
                    + (f" ({len(result.score_kept)} already had better)"
                       if result.score_kept else ""))
    if result.no_role:
        bits.append("not recorded, no role: "
                    + ", ".join(m.gamertag for m in result.no_role))
    if result.gone:
        bits.append(f"{result.gone} no longer on the roster")
    return Applied(True, "; ".join(bits) or "nothing new to record", run=result)


async def set_role(db: Database, request_id: int, role: str) -> bool:
    """An officer settling the role on a request that didn't say one. On a run it
    fills in everyone whose role was unclear; roles that were given stay put."""
    row = await get(db, request_id)
    if row is None or row["status"] != "pending" or role not in ROLES_FOR_CLEARS:
        return False
    if row["kind"] == "run":
        payload = json.loads(row["payload"] or "{}")
        payload["members"] = [[mid, r if r in ROLES_FOR_CLEARS else role]
                              for mid, r in payload.get("members", [])]
        await db.run("UPDATE requests SET payload = ? WHERE id = ?",
                     (json.dumps(payload), request_id))
    else:
        await db.run("UPDATE requests SET role = ? WHERE id = ?", (role, request_id))
    return True


def needs_role(row) -> bool:
    if row["kind"] == "run":
        members = json.loads(row["payload"] or "{}").get("members", [])
        return any(r not in ROLES_FOR_CLEARS for _, r in members)
    return row["kind"] == "achievements" and row["role"] not in ROLES_FOR_CLEARS


async def _apply_register(db: Database, row, payload: dict,
                          actor_id: int, actor_name: str, evidence: str = "") -> Applied:
    """Linking a Discord account to a gamertag. Re-validated at approval time so
    a claim raised while the name was free cannot land after somebody else took it."""
    gamertag = (row["subject"] or "").strip()
    claimant = row["requester_id"]
    if not gamertag:
        return Applied(False, "the claim has no gamertag on it")

    existing, matches = await claim_target(db, gamertag)
    if len(matches) > 1:
        return Applied(False, f"**{gamertag}** matches {len(matches)} roster entries: "
                              + ", ".join(f"**{m.gamertag}**" for m in matches)
                              + " — merge them with `/member merge` first.")

    holder = await store.find_member(db, claimant)
    if holder is not None and (existing is None or holder.id != existing.id):
        return Applied(False, f"that Discord account is already **{holder.gamertag}**")

    if existing is not None and existing.discord_id not in (None, claimant):
        return Applied(False, f"**{existing.gamertag}** is already linked to another account")

    if existing is None:
        member = await store.create_member(db, gamertag, claimant)
        summary = f"added **{gamertag}** and linked it"
    else:
        await db.run("UPDATE members SET discord_id = ?, active = 1 WHERE id = ?",
                     (claimant, existing.id))
        member = await store.get_member(db, existing.id)
        summary = f"linked **{gamertag}**"

    await store.log_action(
        db, actor_id, actor_name, "roster claim", f"{gamertag} -> <@{claimant}>",
        undo=[("UPDATE members SET discord_id = NULL WHERE id = ?", (member.id,))],
        evidence=evidence)
    return Applied(True, summary, member)


async def pending_about(db: Database, member_id: int) -> list[int]:
    """Requests still waiting that would touch this member when decided."""
    ids = []
    for r in await db.all("SELECT id, member_id, payload FROM requests WHERE status = 'pending'"):
        payload = json.loads(r["payload"] or "{}")
        named = {entry[0] for entry in payload.get("members", []) + payload.get("recorded", [])}
        named |= set(payload.get("score_members", [])) | set(payload.get("score_raised", []))
        if r["member_id"] == member_id or member_id in named:
            ids.append(r["id"])
    return ids


async def claim_target(db: Database, gamertag: str):
    """The record a claim on this gamertag lands on, and every record it could
    mean. More than one means two entries differ only by a dot, and a claim can't
    pick between them: an officer merges them first."""
    matches = await store.same_gamertag(db, gamertag)
    return (matches[0] if len(matches) == 1 else None), matches


async def inherits(db: Database, gamertag: str) -> tuple[int, int]:
    """What a claim on an existing gamertag would hand over: (achievements, scores).
    Officers need to see this before approving an identity claim."""
    member, _ = await claim_target(db, gamertag)
    if member is None:
        return (0, 0)
    return (
        await db.val("SELECT COUNT(*) FROM member_achievements WHERE member_id = ?",
                     (member.id,), 0),
        await db.val("SELECT COUNT(*) FROM scores WHERE member_id = ?", (member.id,), 0),
    )
