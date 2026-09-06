"""Leaderboards and guild-wide stats."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, views
from ..checks import viewer_only
from ..config import POINTS, ROLE_LABEL, ROLES

PER_PAGE = 10
BOARDS = [
    app_commands.Choice(name="Most achievements", value="achievements"),
    app_commands.Choice(name="Points", value="points"),
    app_commands.Choice(name="Trial score", value="score"),
    app_commands.Choice(name="Parse", value="parse"),
]


class Leaderboards(commands.Cog):
    """Leaderboards"""

    def __init__(self, bot):
        self.bot = bot

    async def fetch(self, board: str, role: str | None, target: str | None):
        db = self.bot.db
        if board == "points":
            cases = " + ".join(
                f"{weight} * SUM(CASE WHEN a.kind = '{kind}' THEN 1 ELSE 0 END)"
                for kind, weight in POINTS.items()
            )
            rows = await db.all(
                f"SELECT m.gamertag, {cases} AS v FROM members m "
                "JOIN member_achievements ma ON ma.member_id = m.id "
                "JOIN achievements a ON a.key = ma.achievement_key "
                "WHERE m.active = 1 GROUP BY m.id ORDER BY v DESC")
            return [(r["gamertag"], f"{r['v']:,} pts") for r in rows], "Every achievement counts, hard modes and titles count more"

        if board == "achievements":
            clause = "AND ma.role = ?" if role else ""
            args = (role,) if role else ()
            rows = await db.all(
                f"SELECT m.gamertag, COUNT(*) AS v FROM members m "
                f"JOIN member_achievements ma ON ma.member_id = m.id {clause} "
                "WHERE m.active = 1 GROUP BY m.id ORDER BY v DESC", args)
            sub = f"{ROLE_LABEL[role]} only" if role else "All roles combined"
            return [(r["gamertag"], f"{r['v']} achievements") for r in rows], sub

        if board == "score":
            if target:
                rows = await db.all(
                    "SELECT m.gamertag, s.score AS v FROM scores s "
                    "JOIN members m ON m.id = s.member_id "
                    "WHERE m.active = 1 AND s.trial_key = ? ORDER BY v DESC", (target,))
                name = await db.val("SELECT name FROM trials WHERE key = ?", (target,), target)
                sub = f"Best recorded score in {name}"
            else:
                rows = await db.all(
                    "SELECT m.gamertag, SUM(s.score) AS v FROM scores s "
                    "JOIN members m ON m.id = s.member_id "
                    "WHERE m.active = 1 GROUP BY m.id ORDER BY v DESC")
                sub = "Total score across every trial"
            return [(r["gamertag"], f"{r['v']:,}") for r in rows], sub

        # parse
        if target:
            rows = await db.all(
                "SELECT m.gamertag, p.dps AS v FROM parses p JOIN members m ON m.id = p.member_id "
                "WHERE m.active = 1 AND p.label = ? ORDER BY v DESC", (target,))
            sub = f"Best parse on {target}"
        else:
            rows = await db.all(
                "SELECT m.gamertag, MAX(p.dps) AS v FROM parses p "
                "JOIN members m ON m.id = p.member_id "
                "WHERE m.active = 1 GROUP BY m.id ORDER BY v DESC")
            sub = "Everybody's best parse"
        return [(r["gamertag"], f"{r['v']:,} dps") for r in rows], sub

    @app_commands.command(description="Who's on top")
    @app_commands.describe(trial="For the score board", parse_target="For the parse board",
                           role="For the achievements board")
    @app_commands.choices(board=BOARDS,
                          role=[app_commands.Choice(name=ROLE_LABEL[r], value=r) for r in ROLES])
    @app_commands.autocomplete(trial=autocomplete.trials, parse_target=autocomplete.parse_labels)
    @viewer_only()
    async def leaderboard(self, interaction: discord.Interaction,
                          board: app_commands.Choice[str] | None = None,
                          role: app_commands.Choice[str] | None = None,
                          trial: str | None = None, parse_target: str | None = None):
        kind = board.value if board else "points"
        entries, subtitle = await self.fetch(kind, role.value if role else None,
                                             trial or parse_target)
        if not entries:
            return await interaction.response.send_message(
                embed=embeds.warn("No data for that board yet."), ephemeral=True)

        title = dict((c.value, c.name) for c in BOARDS)[kind]
        pages = [
            embeds.leaderboard_embed(
                self.bot.brand, title, subtitle, entries[i:i + PER_PAGE],
                i // PER_PAGE + 1, (len(entries) + PER_PAGE - 1) // PER_PAGE, i)
            for i in range(0, len(entries), PER_PAGE)
        ]
        view = views.Paginator(interaction.user.id, pages)
        await interaction.response.send_message(embed=pages[0], view=view)
        view.message = await interaction.original_response()

    @app_commands.command(description="How the guild is doing overall")
    @viewer_only()
    async def stats(self, interaction: discord.Interaction):
        db = self.bot.db
        members = await db.val("SELECT COUNT(*) FROM members WHERE active = 1", (), 0)
        linked = await db.val(
            "SELECT COUNT(*) FROM members WHERE active = 1 AND discord_id IS NOT NULL", (), 0)
        grants = await db.val("SELECT COUNT(*) FROM member_achievements", (), 0)
        rows = await db.all(
            """
            SELECT t.emoji, t.short, t.name,
                   COUNT(DISTINCT ma.member_id) AS cleared
            FROM trials t
            JOIN achievements a ON a.trial_key = t.key AND a.kind = 'clear'
            LEFT JOIN member_achievements ma ON ma.achievement_key = a.key
                 AND ma.member_id IN (SELECT id FROM members WHERE active = 1)
            GROUP BY t.key ORDER BY t.sort
            """)
        e = embeds.base(self.bot.brand, f"📈  {self.bot.brand.name} at a glance")
        e.add_field(name="Members", value=f"**{members}**\n{linked} linked to Discord")
        e.add_field(name="Achievements recorded", value=f"**{grants:,}**")
        body = "\n".join(
            f"{r['emoji']} `{r['short']:<5}` {embeds.bar(r['cleared'], members)} "
            f"**{r['cleared']}/{members}**"
            for r in rows if r["short"] != "AWA")
        e.add_field(name="Members who have cleared each trial", value=body or "—", inline=False)
        await interaction.response.send_message(embed=e)


async def setup(bot):
    await bot.add_cog(Leaderboards(bot))
