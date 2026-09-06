"""Keeping Discord roles in step with the record.

The role_map table already says which Discord role means which achievement, so
the same table can drive the relationship the other way: earn Godslayer, get the
Godslayer role. Only roles that appear in role_map are ever touched - a role the
bot doesn't know about is none of its business."""
from __future__ import annotations

from dataclasses import dataclass, field

import discord

from . import store
from .db import Database


@dataclass
class SyncPlan:
    member: store.Member
    add: list[discord.Role] = field(default_factory=list)
    remove: list[discord.Role] = field(default_factory=list)
    blocked: list[discord.Role] = field(default_factory=list)   # too high, or no permission
    skipped: str = ""                                           # why there's nothing to do

    @property
    def changes(self) -> int:
        return len(self.add) + len(self.remove)


async def achievement_roles(db: Database) -> dict[str, int]:
    """achievement key -> Discord role id"""
    rows = await db.all("SELECT value, discord_role_id FROM role_map WHERE kind = 'achievement'")
    return {r[0]: r[1] for r in rows}


def can_manage(guild: discord.Guild, role: discord.Role) -> bool:
    """Discord only lets a bot hand out roles below its own highest role."""
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return False
    return not role.managed and not role.is_default() and role < me.top_role


async def plan_member(db: Database, guild: discord.Guild, member: store.Member,
                      mapping: dict[str, int] | None = None) -> SyncPlan:
    """`mapping` lets a bulk sync read the role map once instead of per member."""
    plan = SyncPlan(member)
    if member.discord_id is None:
        plan.skipped = "no Discord account linked"
        return plan
    person = guild.get_member(member.discord_id)
    if person is None:
        plan.skipped = "not in the server"
        return plan

    if mapping is None:
        mapping = await achievement_roles(db)
    earned = {
        r[0] for r in await db.all(
            "SELECT DISTINCT achievement_key FROM member_achievements WHERE member_id = ?",
            (member.id,))
    }
    managed = set(mapping.values())
    held = {r.id for r in person.roles}
    should_hold = {mapping[key] for key in earned if key in mapping}

    for role_id in should_hold - held:
        role = guild.get_role(role_id)
        if role is not None:
            (plan.add if can_manage(guild, role) else plan.blocked).append(role)
    for role_id in (managed & held) - should_hold:
        role = guild.get_role(role_id)
        if role is not None:
            (plan.remove if can_manage(guild, role) else plan.blocked).append(role)
    return plan


async def apply_plan(guild: discord.Guild, plan: SyncPlan, reason: str) -> str:
    """Returns "" on success, or a human-readable problem."""
    person = guild.get_member(plan.member.discord_id) if plan.member.discord_id else None
    if person is None or not plan.changes:
        return ""
    try:
        # Atomic (the default) touches one named role per request. The bulk
        # alternative PATCHes the member's whole role list, which Discord rejects
        # outright if that list contains an integration role - so anybody with
        # Nitro Boost would silently fail to sync. Slower, but it always works.
        if plan.add:
            await person.add_roles(*plan.add, reason=reason)
        if plan.remove:
            await person.remove_roles(*plan.remove, reason=reason)
    except discord.Forbidden:
        return ("I don't have permission to change that member's roles - check that "
                "the bot has **Manage Roles** and that its own role sits above the "
                "achievement roles in Server Settings → Roles.")
    except discord.HTTPException as exc:
        return f"Discord refused the change ({exc.status})."
    return ""
