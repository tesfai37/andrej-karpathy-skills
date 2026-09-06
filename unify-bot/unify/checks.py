"""Permission helpers. Server administrators always pass; beyond that, access is
driven by the role lists an admin sets with /config."""
from __future__ import annotations

import discord
from discord import app_commands


async def _has_configured_role(interaction: discord.Interaction, setting: str) -> bool:
    ids = await interaction.client.db.get_setting(setting, []) or []
    if not ids:
        return False
    mine = {r.id for r in getattr(interaction.user, "roles", [])}
    return bool(mine & {int(i) for i in ids})


async def user_is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_guild):
        return True
    return await _has_configured_role(interaction, "admin_roles")


async def user_can_view(interaction: discord.Interaction) -> bool:
    if await user_is_admin(interaction):
        return True
    allowed = await interaction.client.db.get_setting("viewer_roles", []) or []
    if not allowed:          # not configured = open to the whole server
        return True
    return await _has_configured_role(interaction, "viewer_roles")


class NotAllowed(app_commands.CheckFailure):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await user_is_admin(interaction):
            return True
        raise NotAllowed(
            "This one is for admins. Ask an officer, or have them add your role with "
            "`/config set key:admin_roles`."
        )
    return app_commands.check(predicate)


def viewer_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await user_can_view(interaction):
            return True
        raise NotAllowed("You don't have a role that can look up guild records yet.")
    return app_commands.check(predicate)
