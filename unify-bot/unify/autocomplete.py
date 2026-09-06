"""Shared autocompletes so every command that takes a member, trial or
achievement behaves identically."""
from __future__ import annotations

import discord
from discord import app_commands

from . import store


async def members(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    db = interaction.client.db
    found = await store.search_members(db, current, limit=25)
    choices = []
    for m in found:
        label = m.gamertag
        if m.discord_id and interaction.guild:
            user = interaction.guild.get_member(m.discord_id)
            if user:
                label = f"{m.gamertag}  ({user.display_name})"
        choices.append(app_commands.Choice(name=label[:100], value=m.gamertag))
    return choices


async def achievements(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await interaction.client.db.all(
        """
        SELECT a.key, a.name, t.short FROM achievements a
        JOIN trials t ON t.key = a.trial_key
        WHERE a.key LIKE ? OR a.name LIKE ? OR t.short LIKE ?
        ORDER BY t.sort, a.sort LIMIT 25
        """,
        (f"%{current}%", f"%{current}%", f"%{current}%"),
    )
    return [
        app_commands.Choice(name=f"{r['short']} — {r['name']}  ({r['key']})"[:100], value=r["key"])
        for r in rows
    ]


async def trials(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await interaction.client.db.all(
        "SELECT key, name, short FROM trials WHERE key LIKE ? OR name LIKE ? OR short LIKE ? "
        "ORDER BY sort LIMIT 25",
        (f"%{current}%", f"%{current}%", f"%{current}%"),
    )
    return [app_commands.Choice(name=f"{r['short']} — {r['name']}"[:100], value=r["key"]) for r in rows]


async def parse_labels(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await interaction.client.db.all(
        "SELECT DISTINCT label FROM parses WHERE label LIKE ? ORDER BY label LIMIT 25",
        (f"%{current}%",),
    )
    return [app_commands.Choice(name=r[0][:100], value=r[0]) for r in rows]


async def settings_keys(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    from .config import SETTING_DEFAULTS
    return [
        app_commands.Choice(name=f"{k} — {meta[2]}"[:100], value=k)
        for k, meta in SETTING_DEFAULTS.items()
        if current.lower() in k.lower()
    ][:25]


async def achievement_list(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for options that take several codes at once.

    Completes the last word and hands back the whole string, so picking three
    achievements in a row builds "vsshm vkahm vrghm" instead of replacing it."""
    prefix, _, tail = current.rpartition(" ")
    rows = await interaction.client.db.all(
        """
        SELECT a.key, a.name, t.short FROM achievements a
        JOIN trials t ON t.key = a.trial_key
        WHERE a.key LIKE ? OR a.name LIKE ? OR t.short LIKE ?
        ORDER BY t.sort, a.sort LIMIT 25
        """,
        (f"%{tail}%", f"%{tail}%", f"%{tail}%"),
    )
    out = []
    for r in rows:
        value = f"{prefix} {r['key']}".strip()
        if len(value) > 100:
            continue
        out.append(app_commands.Choice(name=f"{value}  ({r['short']} {r['name']})"[:100],
                                       value=value))
    return out
