"""Everything a normal member uses to look someone up."""
from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, reviews, store, views
from ..checks import viewer_only
from ..config import ROLES
from .reviewqueue import post_card

MAX_OPEN_REPORTS = 3        # per member, so nobody can flood the queue


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
        # A spot check is secret from the recorder it checks, and nobody is told
        # they've been reported; a member only sees a report they sent themselves.
        if r["kind"] == "check" or (r["kind"] == "report" and r["requester_id"] != discord_id):
            continue
        in_run = member is not None and any(
            member_id == member.id
            for member_id, _ in json.loads(r["payload"] or "{}").get("members", []))
        if r["requester_id"] == discord_id or in_run or (
                member is not None and r["member_id"] == member.id):
            mine.append(f"`#{r['id']}` {reviews.KIND_LABEL.get(r['kind'], r['kind'])}"
                        + (f" — {r['note']}" if r["note"] else ""))
    return mine


class ReportModal(discord.ui.Modal, title="Report a mistake"):
    what = discord.ui.Label(
        text="What's wrong?",
        description="An officer reads this. Nothing changes unless they decide it should.",
        component=discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=400,
                                       placeholder="e.g. They weren't in Friday's vSS HM run"))

    def __init__(self, member: store.Member, looking_at: str):
        super().__init__()
        self.member, self.looking_at = member, looking_at

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.client.get_cog("Profiles").file_report(
            interaction, self.member, self.looking_at, self.what.component.value)


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

    # ------------------------------------------------------------------ reports
    async def report_blocked(self, user_id: int, member: store.Member) -> discord.Embed | None:
        db = self.bot.db
        if await db.val("SELECT 1 FROM requests WHERE status = 'pending' AND kind = 'report' "
                        "AND requester_id = ? AND member_id = ?", (user_id, member.id)):
            return embeds.info(self.bot.brand, f"You've already reported **{member.gamertag}**'s "
                                               "record. An officer will look at it.")
        waiting = await db.val("SELECT COUNT(*) FROM requests WHERE status = 'pending' "
                               "AND kind = 'report' AND requester_id = ?", (user_id,), 0)
        if waiting >= MAX_OPEN_REPORTS:
            return embeds.warn(f"You have {waiting} reports waiting already. Once an officer "
                               "has looked at those, you can send more.")
        return None

    async def start_report(self, interaction: discord.Interaction, member: store.Member,
                           looking_at: str) -> None:
        blocked = await self.report_blocked(interaction.user.id, member)
        if blocked is not None:
            return await interaction.response.send_message(embed=blocked, ephemeral=True)
        await interaction.response.send_modal(ReportModal(member, looking_at))

    async def file_report(self, interaction: discord.Interaction, member: store.Member,
                          looking_at: str, text: str) -> None:
        """A flag for the officers. It never changes the record itself."""
        blocked = await self.report_blocked(interaction.user.id, member)
        if blocked is not None:
            return await interaction.response.send_message(embed=blocked, ephemeral=True)
        request_id = await reviews.open_request(
            self.bot.db, "report", requester_id=interaction.user.id, member_id=member.id,
            payload={"text": text.strip(), "context": looking_at}, note="reported by a member")
        channel = await self.bot.channel_for("audit_channel", "problems_channel")
        if channel is not None:
            await post_card(self.bot, await reviews.get(self.bot.db, request_id), channel=channel)
        await interaction.response.send_message(
            embed=embeds.success(f"Thanks. Report `#{request_id}` is with the officers. Nothing "
                                 f"changes on **{member.gamertag}**'s record unless they decide "
                                 "it should."), ephemeral=True)

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
