"""/find - the question a raid lead actually asks.

"Every DPS with vSS hard mode who hasn't got Godslayer yet, parsing over 100k."
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, views
from ..checks import viewer_only
from ..config import ROLES, ROLE_LABEL


class MentionButton(discord.ui.Button):
    """Raid leads want to paste the list into a ping. Code block = no surprise @s."""

    def __init__(self, mentions: list[str]):
        super().__init__(label="Copy as @mentions", emoji="📋",
                         style=discord.ButtonStyle.secondary,
                         disabled=not mentions, row=1)
        self.mentions = mentions

    async def callback(self, interaction: discord.Interaction):
        chunks, current = [], ""
        for mention in self.mentions:          # never split a mention in half
            if len(current) + len(mention) + 1 > 1900:
                chunks.append(current)
                current = ""
            current += mention + " "
        chunks = [c.strip() for c in chunks + [current] if c.strip()] or ["—"]
        await interaction.response.send_message(
            embed=embeds.info(interaction.client.brand,
                              f"{len(self.mentions)} member(s) — copy and paste:"),
            ephemeral=True)
        for chunk in chunks:
            await interaction.followup.send(f"```\n{chunk}\n```", ephemeral=True)


class Find(commands.Cog):
    """Find members"""

    def __init__(self, bot):
        self.bot = bot

    async def validate(self, text: str) -> tuple[list[str], list[str]]:
        known = {r[0] for r in await self.bot.db.all("SELECT key FROM achievements")}
        words = text.replace(",", " ").split()
        return [w for w in words if w in known], [w for w in words if w not in known]

    async def search(self, has, missing, role, min_parse, parse_label, mark=None):
        where = ["m.active = 1"]
        args: list = []
        role_clause = " AND ma.role = ?" if role else ""
        mark_clause = " AND ma.mark = ?" if mark else ""

        for key in has:
            where.append("EXISTS (SELECT 1 FROM member_achievements ma "
                         "WHERE ma.member_id = m.id AND ma.achievement_key = ?"
                         f"{role_clause}{mark_clause})")
            args.append(key)
            if role:
                args.append(role)
            if mark:
                args.append(mark)
        for key in missing:
            where.append("NOT EXISTS (SELECT 1 FROM member_achievements ma "
                         f"WHERE ma.member_id = m.id AND ma.achievement_key = ?{role_clause})")
            args.append(key)
            if role:
                args.append(role)
        if min_parse:
            clause = "EXISTS (SELECT 1 FROM parses p WHERE p.member_id = m.id AND p.dps >= ?"
            args.append(min_parse)
            if parse_label:
                clause += " AND p.label = ?"
                args.append(parse_label)
            where.append(clause + ")")

        return await self.bot.db.all(
            "SELECT m.gamertag, m.discord_id, "
            "  (SELECT COUNT(*) FROM member_achievements ma WHERE ma.member_id = m.id) AS n "
            "FROM members m WHERE " + " AND ".join(where) + " ORDER BY n DESC, m.gamertag",
            args)

    @app_commands.command(description="Find members by what they have (and haven't) cleared")
    @app_commands.describe(
        has="Codes they must all have, e.g. 'vsshm vkahm'",
        missing="Codes they must not have yet, e.g. 'vssgodslayer'",
        role="Only count clears done as this role",
        min_parse="Only members parsing at least this",
        parse_label="…on this specific parse, e.g. 'Arcanist'",
        marked="Only count normal clears, or only the older legacy records",
    )
    @app_commands.choices(
        role=[app_commands.Choice(name=ROLE_LABEL[r], value=r) for r in ROLES],
        marked=[app_commands.Choice(name="Cleared (X)", value="X"),
                app_commands.Choice(name="Legacy record (L) — pre account-wide", value="L")])
    @app_commands.autocomplete(has=autocomplete.achievement_list,
                               missing=autocomplete.achievement_list,
                               parse_label=autocomplete.parse_labels)
    @viewer_only()
    async def find(self, interaction: discord.Interaction, has: str = "", missing: str = "",
                   role: app_commands.Choice[str] | None = None,
                   min_parse: app_commands.Range[int, 1, 500_000] | None = None,
                   parse_label: str | None = None,
                   marked: app_commands.Choice[str] | None = None):
        has_keys, bad_has = await self.validate(has)
        missing_keys, bad_missing = await self.validate(missing)
        unknown = bad_has + bad_missing

        if not (has_keys or missing_keys or min_parse):
            return await interaction.response.send_message(
                embed=embeds.warn(
                    "Give me something to filter on — `has`, `missing` or `min_parse`.\n"
                    "Example: `/find has:vsshm missing:vssgodslayer role:DPS`\n"
                    "For the whole roster, use `/roster`."),
                ephemeral=True)
        if unknown:
            return await interaction.response.send_message(
                embed=embeds.error(
                    f"I don't know `{'`, `'.join(unknown)}`. Pick from the suggestions as you "
                    "type — `/map missing` lists every code."),
                ephemeral=True)

        await interaction.response.defer(thinking=True)
        rows = await self.search(has_keys, missing_keys,
                                 role.value if role else None, min_parse, parse_label,
                                 marked.value if marked else None)

        criteria = []
        if has_keys:
            criteria.append("✅ has " + ", ".join(f"`{k}`" for k in has_keys))
        if missing_keys:
            criteria.append("🚫 missing " + ", ".join(f"`{k}`" for k in missing_keys))
        if role:
            criteria.append(f"{ROLE_LABEL[role.value]} clears only")
        if min_parse:
            criteria.append(f"⚔️ parsing {min_parse:,}+"
                            + (f" on {parse_label}" if parse_label else ""))
        if marked:
            label = await self.bot.setting("mark_label")
            criteria.append(f"🅛 marked {label}" if marked.value == "L" else "✅ cleared (X)")
        subtitle = "  •  ".join(criteria)

        if not rows:
            return await interaction.followup.send(embed=embeds.warn(
                f"Nobody matches.\n{subtitle}"))

        lines = [
            f"**{r['gamertag']}**" + (f" — <@{r['discord_id']}>" if r["discord_id"] else "")
            for r in rows
        ]
        per_page = 15
        pages = []
        for start in range(0, len(lines), per_page):
            e = embeds.base(self.bot.brand, f"🔎  {len(rows)} match"
                                            f"{'es' if len(rows) != 1 else ''}", subtitle)
            e.add_field(name="​", value="\n".join(lines[start:start + per_page]), inline=False)
            pages.append(e)
        for i, e in enumerate(pages, 1):
            e.set_footer(text=f"{self.bot.brand.name} • page {i}/{len(pages)}")

        view = views.Paginator(interaction.user.id, pages)
        view.add_item(MentionButton([f"<@{r['discord_id']}>" for r in rows if r["discord_id"]]))
        message = await interaction.followup.send(embed=pages[0], view=view, wait=True)
        view.message = message


async def setup(bot):
    await bot.add_cog(Find(bot))
