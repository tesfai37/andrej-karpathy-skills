"""Welcome card when somebody joins."""
from __future__ import annotations

import discord
from discord.ext import commands

from .. import embeds, store


class Welcome(commands.Cog):
    """Welcome"""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel_id = await self.bot.setting("welcome_channel")
        channel = self.bot.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            return
        known = await store.find_member(self.bot.db, member.id)
        e = embeds.base(
            self.bot.brand, f"Welcome to {self.bot.brand.name}, {member.display_name}!",
            f"{member.mention} just joined.\n\n"
            "• `/me` — your achievement card\n"
            "• `/profile` — look anyone up\n"
            "• `/leaderboard` — see where you stand\n"
            "• `/help` — everything else",
        )
        e.set_thumbnail(url=member.display_avatar.url)
        if known:
            e.add_field(name="Roster", value=f"Already linked as **{known.gamertag}**.",
                        inline=False)
        else:
            e.add_field(name="Roster",
                        value="An officer will add you with `/member add` so your clears "
                              "get tracked.", inline=False)
        await channel.send(embed=e)


async def setup(bot):
    await bot.add_cog(Welcome(bot))
