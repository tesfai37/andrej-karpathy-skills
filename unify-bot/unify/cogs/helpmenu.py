"""/help - a menu, not a wall of text."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import embeds, views
from ..checks import user_is_admin

QUICKSTART = (
    "**Just want to look someone up?**\n"
    "Type `/profile` and start typing their gamertag — the list narrows as you type. "
    "Then use the menus on the card to switch role or open a trial.\n\n"
    "**Want your own card?** `/me`\n"
    "**Who's on top?** `/leaderboard`\n"
    "**Building a group?** `/find has:vsshm missing:vssgodslayer role:DPS`\n\n"
    "Pick a section below for the full list."
)

SECTIONS: list[tuple[str, str, bool, list[tuple[str, str]]]] = [
    ("Look people up", "🔎", False, [
        ("/profile", "Somebody's achievements, with menus to explore"),
        ("/me", "Your own card"),
        ("/score", "Their trial scores"),
        ("/parse", "Their parse numbers"),
        ("/find", "Who has (and hasn't) cleared what — for building a group"),
        ("/roster", "Everyone in the guild"),
    ]),
    ("Leaderboards", "📊", False, [
        ("/leaderboard", "Points, achievements, scores or parses"),
        ("/stats", "How the guild is doing overall"),
    ]),
    ("Admin · people", "👥", True, [
        ("/member add", "Put somebody on the roster"),
        ("/member link", "Connect a gamertag to a Discord account"),
        ("/member rename", "Change a gamertag"),
        ("/member remove", "Take somebody off the roster"),
    ]),
    ("Admin · achievements", "🏅", True, [
        ("/achievement give", "Give achievements (prerequisites fill in automatically)"),
        ("/achievement take", "Remove achievements"),
        ("/record score", "Record a trial score"),
        ("/record parse", "Record a parse"),
        ("/trial add", "Add a brand new trial"),
        ("/achievement new", "Add a new achievement to a trial"),
        ("/achievement rename", "Change how an achievement is displayed"),
    ]),
    ("Admin · setup", "🔗", True, [
        ("/setup", "The one-screen setup — start here"),
        ("/map auto", "Match your Discord roles to achievements automatically"),
        ("/map role", "Map one role, guided, no typing"),
        ("/map list", "See every mapping"),
        ("/map missing", "Achievements that still need a role"),
        ("/sync check", "Can I hand out your achievement roles?"),
        ("/sync all", "Give everyone the roles they've earned"),
        ("/sync member", "Fix one person's roles"),
        ("/config show", "Everything the bot is set to"),
        ("/config channel", "Point a setting at a channel"),
        ("/config roles", "Who counts as an admin or viewer"),
    ]),
    ("Admin · data", "💾", True, [
        ("/import", "Drag a spreadsheet in and I'll read it"),
        ("/export", "Download everything as Excel, CSV or the raw database"),
        ("/audit", "Every change, who made it and when"),
        ("/undo", "Reverse the last change"),
    ]),
]


class HelpView(views.OwnedView):
    def __init__(self, bot, owner_id: int, admin: bool):
        super().__init__(owner_id)
        self.bot = bot
        self.sections = [s for s in SECTIONS if admin or not s[2]]
        self.add_item(SectionSelect(self.sections))

    def page(self, index: int | None) -> discord.Embed:
        if index is None:
            e = embeds.base(self.bot.brand, f"❓  {self.bot.brand.name} bot", QUICKSTART)
            e.add_field(name="Sections",
                        value="\n".join(f"{emoji} {name}" for name, emoji, _, _ in self.sections),
                        inline=False)
            return e
        name, emoji, _, commands_ = self.sections[index]
        return embeds.base(
            self.bot.brand, f"{emoji}  {name}",
            "\n".join(f"**`{cmd}`**\n　{desc}" for cmd, desc in commands_))


class SectionSelect(discord.ui.Select):
    def __init__(self, sections):
        super().__init__(
            placeholder="Pick a section…",
            options=[discord.SelectOption(label=name, value=str(i), emoji=emoji)
                     for i, (name, emoji, _, _) in enumerate(sections)],
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=self.view.page(int(self.values[0])), view=self.view)


class Help(commands.Cog):
    """Help"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="What this bot can do")
    async def help(self, interaction: discord.Interaction):
        view = HelpView(self.bot, interaction.user.id, await user_is_admin(interaction))
        await interaction.response.send_message(embed=view.page(None), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Help(bot))
