"""Everything a normal member uses to look someone up."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, store, views
from ..checks import viewer_only
from ..config import ROLES


async def resolve(interaction: discord.Interaction, member: str | None,
                  user: discord.Member | None) -> store.Member | None:
    """Commands accept either a picked Discord user or a typed gamertag."""
    db = interaction.client.db
    if user is not None:
        return await store.find_member(db, user.id)
    if member:
        return await store.find_member(db, member)
    return await store.find_member(db, interaction.user.id)


NOT_FOUND = (
    "I don't have a record for that person yet.\n"
    "Ask an admin to run `/member add` — it takes ten seconds."
)


class Profiles(commands.Cog):
    """Look up members"""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ profile
    @app_commands.command(description="Look up someone's trial achievements")
    @app_commands.describe(
        member="Start typing a gamertag",
        user="…or just pick them from the server",
        role="Which role to open on (default DPS)",
    )
    @app_commands.choices(role=[app_commands.Choice(name=r.upper(), value=r) for r in ROLES])
    @app_commands.autocomplete(member=autocomplete.members)
    @viewer_only()
    async def profile(self, interaction: discord.Interaction, member: str | None = None,
                      user: discord.Member | None = None,
                      role: app_commands.Choice[str] | None = None):
        target = await resolve(interaction, member, user)
        if target is None:
            return await interaction.response.send_message(embed=embeds.error(NOT_FOUND),
                                                           ephemeral=True)
        avatar = None
        if target.discord_id and interaction.guild:
            found = interaction.guild.get_member(target.discord_id)
            avatar = found.display_avatar.url if found else None

        view = views.ProfileView(self.bot, interaction.user.id, target,
                                 role.value if role else "dps", avatar)
        embed = await view.build()
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(description="Your own achievement card")
    @viewer_only()
    async def me(self, interaction: discord.Interaction):
        target = await store.find_member(interaction.client.db, interaction.user.id)
        if target is None:
            return await interaction.response.send_message(
                embed=embeds.error(
                    "You're not in the roster yet. Ask an admin to run "
                    "`/member add` with your gamertag."),
                ephemeral=True,
            )
        view = views.ProfileView(self.bot, interaction.user.id, target, "dps",
                                 interaction.user.display_avatar.url)
        embed = await view.build()
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    # ------------------------------------------------------------------ scores
    @app_commands.command(description="Trial scores for a member")
    @app_commands.autocomplete(member=autocomplete.members)
    @viewer_only()
    async def score(self, interaction: discord.Interaction, member: str | None = None,
                    user: discord.Member | None = None):
        target = await resolve(interaction, member, user)
        if target is None:
            return await interaction.response.send_message(embed=embeds.error(NOT_FOUND),
                                                           ephemeral=True)
        rows = await self.bot.db.all(
            "SELECT t.emoji, t.short, t.name, s.score, s.recorded_at FROM scores s "
            "JOIN trials t ON t.key = s.trial_key WHERE s.member_id = ? ORDER BY t.sort",
            (target.id,),
        )
        e = embeds.base(self.bot.brand, f"🏆  Scores — {target.gamertag}")
        if rows:
            e.description = "\n".join(
                f"{r['emoji']} `{r['short']:<5}` **{r['score']:,}**" for r in rows
            )
            e.add_field(name="Total", value=f"**{sum(r['score'] for r in rows):,}**")
        else:
            e.description = "_No scores recorded yet._"
        await interaction.response.send_message(embed=e)

    # ------------------------------------------------------------------ parses
    @app_commands.command(description="Parse numbers for a member")
    @app_commands.autocomplete(member=autocomplete.members)
    @viewer_only()
    async def parse(self, interaction: discord.Interaction, member: str | None = None,
                    user: discord.Member | None = None):
        target = await resolve(interaction, member, user)
        if target is None:
            return await interaction.response.send_message(embed=embeds.error(NOT_FOUND),
                                                           ephemeral=True)
        rows = await self.bot.db.all(
            "SELECT label, dps, recorded_at FROM parses WHERE member_id = ? ORDER BY dps DESC",
            (target.id,),
        )
        e = embeds.base(self.bot.brand, f"⚔️  Parses — {target.gamertag}")
        e.description = "\n".join(
            f"**{r['dps']:,}** — {r['label']}" for r in rows
        ) or "_No parses recorded yet._"
        await interaction.response.send_message(embed=e)

    # ------------------------------------------------------------------ roster
    @app_commands.command(description="Who is in the roster")
    @app_commands.describe(search="Filter by gamertag")
    @viewer_only()
    async def roster(self, interaction: discord.Interaction, search: str | None = None):
        rows = await self.bot.db.all(
            "SELECT m.gamertag, m.discord_id, "
            "  (SELECT COUNT(*) FROM member_achievements ma WHERE ma.member_id = m.id) AS n "
            "FROM members m WHERE m.active = 1 AND m.gamertag LIKE ? ORDER BY m.gamertag",
            (f"%{search or ''}%",),
        )
        if not rows:
            return await interaction.response.send_message(
                embed=embeds.warn("Nobody matches that."), ephemeral=True)

        per_page, pages = 15, []
        for start in range(0, len(rows), per_page):
            chunk = rows[start:start + per_page]
            e = embeds.base(self.bot.brand, f"👥  Roster ({len(rows)})")
            e.description = "\n".join(
                f"**{r['gamertag']}**" + (f" — <@{r['discord_id']}>" if r["discord_id"] else "")
                + f"  ·  {r['n']} achievements"
                for r in chunk
            )
            pages.append(e)
        for i, e in enumerate(pages, 1):
            e.set_footer(text=f"{self.bot.brand.name} • page {i}/{len(pages)}")

        view = views.Paginator(interaction.user.id, pages)
        await interaction.response.send_message(embed=pages[0], view=view)
        view.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(Profiles(bot))
