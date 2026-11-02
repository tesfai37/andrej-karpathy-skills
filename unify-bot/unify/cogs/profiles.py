"""Everything a normal member uses to look someone up."""
from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, reviews, store, views
from ..checks import viewer_only
from ..config import ROLES


async def resolve(interaction: discord.Interaction, member: str | None,
                  user: discord.Member | None) -> store.Lookup:
    """A picked Discord user, a typed gamertag or Discord name, or yourself."""
    db = interaction.client.db
    if user is not None:
        return store.Lookup(await store.find_member(db, user.id))
    if member:
        return await store.lookup_member(db, member, interaction.guild)
    return store.Lookup(await store.find_member(db, interaction.user.id))


def missing(member: str | None, user: discord.Member | None,
            found: store.Lookup) -> discord.Embed:
    if member:
        return embeds.no_match(member, found.candidates)
    if user is not None:
        return embeds.error(f"{user.mention} isn't on the roster yet. "
                            "An officer can add them with `/member add`.")
    return embeds.error("You're not on the roster yet. Run `/register` to ask "
                        "an officer to add you.")


async def waiting_on(db, discord_id: int, member: store.Member | None) -> list[str]:
    """Anything of this person's still waiting on an officer: what they raised,
    what's about them, and runs they're in."""
    mine = []
    for r in await db.all("SELECT * FROM requests WHERE status = 'pending' ORDER BY id"):
        in_run = member is not None and any(
            member_id == member.id
            for member_id, _ in json.loads(r["payload"] or "{}").get("members", []))
        if r["requester_id"] == discord_id or in_run or (
                member is not None and r["member_id"] == member.id):
            mine.append(f"`#{r['id']}` {reviews.KIND_LABEL.get(r['kind'], r['kind'])}"
                        + (f" — {r['note']}" if r["note"] else ""))
    return mine


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
        found = await resolve(interaction, member, user)
        target = found.member
        if target is None:
            return await interaction.response.send_message(
                embed=missing(member, user, found), ephemeral=True)
        avatar = None
        if target.discord_id and interaction.guild:
            found = interaction.guild.get_member(target.discord_id)
            avatar = found.display_avatar.url if found else None

        view = views.ProfileView(self.bot, interaction.user.id, target,
                                 role.value if role else "dps", avatar)
        embed = await view.build()
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(description="Your own card, and anything of yours waiting on an officer")
    @viewer_only()
    async def me(self, interaction: discord.Interaction):
        db = interaction.client.db
        target = await store.find_member(db, interaction.user.id)
        waiting = await waiting_on(db, interaction.user.id, target)
        heading = f"⏳ {len(waiting)} waiting on an officer"
        if target is None:
            e = embeds.base(self.bot.brand, "🪪  Where you stand",
                            "You're not linked to a gamertag yet. Run `/register` with a "
                            "screenshot of your gamertag, and an officer will link you.")
            if waiting:
                e.add_field(name=heading, value=embeds.lines_within(waiting), inline=False)
            e.set_footer(text=f"{self.bot.brand.name} • only you can see this")
            return await interaction.response.send_message(embed=e, ephemeral=True)

        view = views.ProfileView(self.bot, interaction.user.id, target, "dps",
                                 interaction.user.display_avatar.url)
        embed = await view.build()
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        if waiting:
            await interaction.followup.send(
                embed=embeds.base(self.bot.brand, heading, embeds.lines_within(waiting)),
                ephemeral=True)

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
