"""Weekly export + summary, posted automatically. Set the day and hour with
/config set key:report_day and key:report_hour."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from .. import embeds

log = logging.getLogger("unify.reports")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Reports(commands.Cog):
    """Scheduled reports"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=30)
    async def tick(self):
        now = datetime.now(timezone.utc)
        day = await self.bot.setting("report_day")
        if day == "off" or DAYS[now.weekday()] != day:
            return
        if now.hour != int(await self.bot.setting("report_hour")):
            return
        stamp = now.strftime("%Y-%m-%d")
        if await self.bot.db.get_setting("_last_report") == stamp:
            return

        channel_id = await self.bot.setting("reports_channel")
        channel = self.bot.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            return
        await self.bot.db.set_setting("_last_report", stamp)
        try:
            await channel.send(embed=await self.summary(), file=await self.attachment())
        except discord.HTTPException:
            log.warning("weekly report could not be posted")

    @tick.before_loop
    async def before(self):
        await self.bot.wait_until_ready()

    async def summary(self) -> discord.Embed:
        db = self.bot.db
        members = await db.val("SELECT COUNT(*) FROM members WHERE active = 1", (), 0)
        week = await db.all(
            "SELECT m.gamertag, COUNT(*) AS n FROM member_achievements ma "
            "JOIN members m ON m.id = ma.member_id "
            "WHERE ma.granted_at >= datetime('now', '-7 days') "
            "GROUP BY m.id ORDER BY n DESC LIMIT 10")
        total_week = await db.val(
            "SELECT COUNT(*) FROM member_achievements "
            "WHERE granted_at >= datetime('now', '-7 days')", (), 0)
        e = embeds.base(
            self.bot.brand, "🗓️  This week at " + self.bot.brand.name,
            f"**{total_week}** achievements recorded across **{members}** members.")
        if week:
            e.add_field(
                name="Busiest week",
                value="\n".join(f"**{r['gamertag']}** — {r['n']}" for r in week),
                inline=False)
        e.add_field(name="Backup", value="The full database is attached.", inline=False)
        return e

    async def attachment(self) -> discord.File:
        return await self.bot.get_cog("DataIO").build_export("xlsx")


async def setup(bot):
    await bot.add_cog(Reports(bot))
