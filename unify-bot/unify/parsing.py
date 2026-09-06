"""Reads an achievement post out of a normal Discord message.

    @BUDMAN008 dps @vSS @vSS Ice HM
    @someone tank vsshm vkahm

Anything the bot can't map is reported back so an admin can fix the mapping in
one click instead of the post being silently dropped."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import ROLES
from .db import Database

USER_RE = re.compile(r"<@!?(\d{15,25})>")
ROLE_RE = re.compile(r"<@&(\d{15,25})>")
WORD_RE = re.compile(r"[A-Za-z0-9'_-]+")


@dataclass
class Parsed:
    discord_ids: list[int] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    unmapped_role_ids: list[int] = field(default_factory=list)
    leftover_words: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.keys or self.unmapped_role_ids)


async def parse_message(db: Database, content: str) -> Parsed:
    out = Parsed()
    out.discord_ids = [int(m) for m in USER_RE.findall(content)]

    mapping = {
        r["discord_role_id"]: (r["kind"], r["value"])
        for r in await db.all("SELECT * FROM role_map")
    }
    for raw in ROLE_RE.findall(content):
        role_id = int(raw)
        entry = mapping.get(role_id)
        if entry is None:
            out.unmapped_role_ids.append(role_id)
        elif entry[0] == "role":
            out.roles.append(entry[1])
        else:
            out.keys.append(entry[1])

    known_keys = {r[0].lower() for r in await db.all("SELECT key FROM achievements")}
    stripped = ROLE_RE.sub(" ", USER_RE.sub(" ", content))
    for word in WORD_RE.findall(stripped):
        low = word.lower()
        if low in ROLES:
            out.roles.append(low)
        elif low in known_keys:
            out.keys.append(low)
        else:
            out.leftover_words.append(word)

    # de-duplicate, keep order
    out.roles = list(dict.fromkeys(out.roles))
    out.keys = list(dict.fromkeys(out.keys))
    out.unmapped_role_ids = list(dict.fromkeys(out.unmapped_role_ids))
    return out
