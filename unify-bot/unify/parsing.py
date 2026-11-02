"""Reads an achievement post out of a normal Discord message.

One person, the way the guild has always posted:

    @inkeddragonmami healer @vCR Gryphon Heart

or a whole run in one post:

    @vSS HM @Godslayer
    score: 257,498
    tank: @a @b
    healer: @c @d
    dps: @e @f @g @h @i @j @k @l

The rules, so a raid lead can predict them:

* a role word on a line applies to every @mention on that line
* a role word on its own line carries down until the next role word
* mentions with no role nearby take the post's role, if the post has only one
* two roles on one line, or one person under two roles, is left unresolved for
  an officer rather than guessed
* a line containing "score" carries the run's score; its trial is named on that
  line or inferred from the achievements. People and pinged roles on it still
  count, but its typed words don't count as achievements

Anything the bot can't map is reported back so an officer can fix the mapping,
instead of the post being silently dropped."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .db import Database
from .importer import cell_number

USER_RE = re.compile(r"<@!?(\d{15,25})>")
ROLE_RE = re.compile(r"<@&(\d{15,25})>")
WORD_RE = re.compile(r"[A-Za-z0-9'_-]+")
SCORE_RE = re.compile(r"\bscores?\b", re.I)
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*[kKmM]?\b")

ROLE_WORDS = {
    "tank": "tank", "tanks": "tank",
    "healer": "healer", "healers": "healer", "heals": "healer",
    "dps": "dps", "dd": "dps",
}


@dataclass
class Parsed:
    discord_ids: list[int] = field(default_factory=list)          # everyone mentioned, in order
    assignments: list[tuple[int, str]] = field(default_factory=list)  # (discord id, role or "")
    roles: list[str] = field(default_factory=list)                # distinct roles named in the post
    keys: list[str] = field(default_factory=list)
    unmapped_role_ids: list[int] = field(default_factory=list)
    leftover_words: list[str] = field(default_factory=list)
    score: int | None = None
    score_trial: str = ""
    score_note: str = ""          # why the score can't be used as it stands

    @property
    def is_empty(self) -> bool:
        return not (self.keys or self.unmapped_role_ids or self.score)

    def role_for(self, discord_id: int) -> str:
        return dict(self.assignments).get(discord_id, "")


async def parse_message(db: Database, content: str) -> Parsed:
    out = Parsed()
    mapping = {
        r["discord_role_id"]: (r["kind"], r["value"])
        for r in await db.all("SELECT * FROM role_map")
    }
    achievement_trial = {
        r[0].lower(): r[1] for r in await db.all("SELECT key, trial_key FROM achievements")
    }
    trial_names: dict[str, str] = {}
    for key, short in await db.all("SELECT key, short FROM trials"):
        trial_names[key.lower()] = key
        trial_names[short.lower()] = key

    lines: list[tuple[list[int], list[str]]] = []      # (mentions, roles named on that line)
    post_roles: list[str] = []

    for line in content.splitlines() or [content]:
        # The score line still counts its people and pinged roles. Only its
        # typed words stop counting as achievements: "vss" there names the
        # trial, it doesn't grant the clear.
        score_line = out.score is None and bool(SCORE_RE.search(line))
        if score_line:
            read_score(out, line, trial_names)

        mentions = [int(m) for m in USER_RE.findall(line)]
        roles_here: list[str] = []
        for raw in ROLE_RE.findall(line):
            entry = mapping.get(int(raw))
            if entry is None:
                out.unmapped_role_ids.append(int(raw))
            elif entry[0] == "role":
                roles_here.append(entry[1])
            else:
                out.keys.append(entry[1])

        for word in WORD_RE.findall(ROLE_RE.sub(" ", USER_RE.sub(" ", line))):
            low = word.lower()
            if low in ROLE_WORDS:
                roles_here.append(ROLE_WORDS[low])
            elif score_line:
                continue
            elif low in achievement_trial:
                out.keys.append(low)
            else:
                out.leftover_words.append(word)

        roles_here = list(dict.fromkeys(roles_here))
        lines.append((mentions, roles_here))
        post_roles.extend(roles_here)

    out.roles = list(dict.fromkeys(post_roles))
    only_role = out.roles[0] if len(out.roles) == 1 else ""

    assigned: dict[int, str] = {}
    carried = ""
    for mentions, roles_here in lines:
        if len(roles_here) == 1:
            carried = line_role = roles_here[0]
        elif len(roles_here) > 1:
            carried = line_role = ""          # two roles on one line: unclear
        else:
            line_role = carried or only_role
        for discord_id in mentions:
            if discord_id in assigned and assigned[discord_id] != line_role:
                assigned[discord_id] = ""     # one person under two roles: unclear
            else:
                assigned.setdefault(discord_id, line_role)

    out.assignments = list(assigned.items())
    out.discord_ids = list(assigned)
    out.keys = list(dict.fromkeys(out.keys))
    out.unmapped_role_ids = list(dict.fromkeys(out.unmapped_role_ids))

    if out.score is not None and not out.score_trial:
        trials = {achievement_trial[k] for k in out.keys}
        if len(trials) == 1:
            out.score_trial = trials.pop()
        elif not out.score_note:
            out.score_note = "a score was given but I can't tell which trial it's for"
    return out


def read_score(out: Parsed, line: str, trial_names: dict[str, str]) -> None:
    """The score line: one number, and optionally the trial it's for."""
    text = ROLE_RE.sub(" ", USER_RE.sub(" ", line))
    for word in WORD_RE.findall(text):
        if word.lower() in trial_names:
            out.score_trial = trial_names[word.lower()]
            break
    match = NUMBER_RE.search(text)
    value = cell_number(match.group(0)) if match else None
    if value:
        out.score = value
    else:
        out.score = 0
        out.score_note = "the score line has no number I can read"
