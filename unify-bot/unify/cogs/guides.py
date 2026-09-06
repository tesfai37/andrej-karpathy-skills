"""The guild's own reference material - CP builds, channel directories, setups.

The old bot served these with !cp, !link, !setup, !craft and !farming out of
one table per category. Same content, one command, and officers can edit it from
Discord instead of writing SQL."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import embeds, views
from ..checks import admin_only, viewer_only

CATEGORY_EMOJI = {"cp": "🎯", "link": "🔗", "setup": "🧩", "craft": "⚗️", "farming": "🌾"}


async def topic_autocomplete(interaction: discord.Interaction, current: str):
    rows = await interaction.client.db.all(
        "SELECT category, topic FROM guides "
        "WHERE topic LIKE ? OR category LIKE ? ORDER BY category, topic LIMIT 25",
        (f"%{current}%", f"%{current}%"))
    return [
        app_commands.Choice(
            name=f"{CATEGORY_EMOJI.get(r['category'].lower(), '📄')} {r['category']} · {r['topic']}"[:100],
            value=f"{r['category']}/{r['topic']}")
        for r in rows
    ]


async def category_autocomplete(interaction: discord.Interaction, current: str):
    rows = await interaction.client.db.all(
        "SELECT DISTINCT category FROM guides WHERE category LIKE ? ORDER BY category LIMIT 25",
        (f"%{current}%",))
    return [app_commands.Choice(name=r[0], value=r[0]) for r in rows]


class BodyModal(discord.ui.Modal):
    """Multi-line text needs a modal - a slash option can't hold a CP build."""

    body = discord.ui.TextInput(label="Text", style=discord.TextStyle.paragraph,
                                max_length=4000, required=True)

    def __init__(self, cog, category: str, topic: str, existing: str = ""):
        super().__init__(title=f"{category} · {topic}"[:45])
        self.cog, self.category, self.topic = cog, category, topic
        self.body.default = existing[:4000]

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.bot.db.run(
            "INSERT INTO guides(category, topic, body, updated_by) VALUES(?,?,?,?) "
            "ON CONFLICT(category, topic) DO UPDATE SET body = excluded.body, "
            "updated_at = datetime('now'), updated_by = excluded.updated_by",
            (self.category, self.topic, str(self.body), interaction.user.id))
        await self.cog.bot.audit(interaction.user, "guide saved",
                                 f"{self.category} · {self.topic}")
        await interaction.response.send_message(
            embed=embeds.success(f"Saved **{self.category} · {self.topic}**.\n"
                                 f"Anyone can read it with `/guide show`."),
            ephemeral=True)


class Guides(commands.Cog):
    """Guides & reference"""

    def __init__(self, bot):
        self.bot = bot

    group = app_commands.Group(name="guide", description="CP builds, links and guild reference")

    @staticmethod
    def split(value: str) -> tuple[str, str]:
        category, _, topic = value.partition("/")
        return category.strip(), topic.strip()

    @group.command(name="show", description="Look something up")
    @app_commands.describe(topic="Start typing — CP builds, links, setups…")
    @app_commands.autocomplete(topic=topic_autocomplete)
    @viewer_only()
    async def show(self, interaction: discord.Interaction, topic: str):
        category, name = self.split(topic)
        row = await self.bot.db.one(
            "SELECT * FROM guides WHERE category = ? AND topic = ?", (category, name)
        ) or await self.bot.db.one("SELECT * FROM guides WHERE topic = ?", (topic,))
        if row is None:
            return await interaction.response.send_message(
                embed=embeds.error(
                    f"I don't have anything for **{topic}**. `/guide list` shows everything."),
                ephemeral=True)
        e = embeds.base(
            self.bot.brand,
            f"{CATEGORY_EMOJI.get(row['category'].lower(), '📄')}  {row['topic']}",
            row["body"][:4096])
        e.set_footer(text=f"{self.bot.brand.name} • {row['category']} • updated {row['updated_at']}")
        await interaction.response.send_message(embed=e)

    @group.command(name="list", description="Everything the bot can tell you about")
    @app_commands.autocomplete(category=category_autocomplete)
    @viewer_only()
    async def list_cmd(self, interaction: discord.Interaction, category: str | None = None):
        if category:
            rows = await self.bot.db.all(
                "SELECT category, topic FROM guides WHERE category = ? ORDER BY topic",
                (category,))
        else:
            rows = await self.bot.db.all("SELECT category, topic FROM guides "
                                         "ORDER BY category, topic")
        if not rows:
            return await interaction.response.send_message(
                embed=embeds.warn("Nothing saved yet — officers add entries with "
                                  "`/guide save`."), ephemeral=True)

        grouped: dict[str, list[str]] = {}
        for r in rows:
            grouped.setdefault(r["category"], []).append(r["topic"])

        pages, current = [], embeds.base(self.bot.brand, f"📚  Guides ({len(rows)})")
        for name, topics in grouped.items():
            if len(current.fields) == 6:
                pages.append(current)
                current = embeds.base(self.bot.brand, f"📚  Guides ({len(rows)})")
            current.add_field(
                name=f"{CATEGORY_EMOJI.get(name.lower(), '📄')} {name}  ({len(topics)})",
                value=embeds.lines_within([", ".join(f"`{t}`" for t in topics)]),
                inline=False)
        pages.append(current)

        view = views.Paginator(interaction.user.id, pages)
        await interaction.response.send_message(embed=pages[0], view=view)
        view.message = await interaction.original_response()

    @group.command(name="save", description="Add or edit an entry")
    @app_commands.describe(category="e.g. cp, link, setup", topic="e.g. magblade")
    @app_commands.autocomplete(category=category_autocomplete)
    @admin_only()
    async def save(self, interaction: discord.Interaction, category: str, topic: str):
        existing = await self.bot.db.val(
            "SELECT body FROM guides WHERE category = ? AND topic = ?",
            (category.strip(), topic.strip()), "")
        await interaction.response.send_modal(
            BodyModal(self, category.strip(), topic.strip(), existing))

    @group.command(name="delete", description="Remove an entry")
    @app_commands.autocomplete(topic=topic_autocomplete)
    @admin_only()
    async def delete(self, interaction: discord.Interaction, topic: str):
        category, name = self.split(topic)
        cur = await self.bot.db.run(
            "DELETE FROM guides WHERE category = ? AND topic = ?", (category, name))
        if not cur.rowcount:
            return await interaction.response.send_message(
                embed=embeds.warn("Nothing matched that."), ephemeral=True)
        await self.bot.audit(interaction.user, "guide deleted", f"{category} · {name}")
        await interaction.response.send_message(
            embed=embeds.success(f"Deleted **{category} · {name}**."), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Guides(bot))
