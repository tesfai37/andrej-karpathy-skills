"""Logging that two people can actually keep up with.

Three audiences, three treatments:

* **activity** - routine recordings, batched into a digest so a raid night is a
  handful of messages rather than hundreds
* **admin** - officer data changes, one per action, via bot.audit
* **problems** - only what needs a human, posted the moment it happens

Plus a morning briefing, which is the one thing an officer should have to read."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from .. import embeds, reviews

log = logging.getLogger("unify.logs")


async def recorder_week(db) -> list[str]:
    """Who recorded what in the last seven days, one line each.

    Recorders write without a review, so this is the check on them: a recorder
    who mostly records the same few people stands out here."""
    rows = await db.all(
        "SELECT ma.granted_by AS who, COUNT(*) AS recorded, "
        "COUNT(DISTINCT ma.member_id) AS people, SUM(a.kind = 'title') AS titles "
        "FROM member_achievements ma JOIN achievements a ON a.key = ma.achievement_key "
        "WHERE ma.granted_by IS NOT NULL AND ma.granted_at >= datetime('now', '-7 days') "
        "GROUP BY ma.granted_by ORDER BY recorded DESC")
    # Scores are overwritten by better ones, so they're counted from the audit log.
    scores = {r[0]: r[1] for r in await db.all(
        "SELECT actor_id, COUNT(*) FROM audit_log WHERE action = 'score' AND undone = 0 "
        "AND actor_id IS NOT NULL AND ts >= datetime('now', '-7 days') GROUP BY actor_id")}
    lines = []
    for r in rows:
        line = (f"<@{r['who']}> — **{r['recorded']}** achievements for **{r['people']}** "
                f"{'person' if r['people'] == 1 else 'people'}"
                + (f" · {r['titles']} title{'s' if r['titles'] != 1 else ''}" if r["titles"] else "")
                + (f" · {scores[r['who']]} score{'s' if scores[r['who']] != 1 else ''}"
                   if r["who"] in scores else ""))
        if r["recorded"] >= 10:
            top = await db.all(
                "SELECT m.gamertag, COUNT(*) AS n FROM member_achievements ma "
                "JOIN members m ON m.id = ma.member_id "
                "WHERE ma.granted_by = ? AND ma.granted_at >= datetime('now', '-7 days') "
                "GROUP BY ma.member_id ORDER BY n DESC LIMIT 3", (r["who"],))
            share = sum(t["n"] for t in top) / r["recorded"]
            if share >= 0.6:
                line += (f"\n  ⚠️ {share:.0%} of it went to "
                         f"{', '.join(t['gamertag'] for t in top)} — worth a look")
        lines.append(line)
    recorders = {r["who"] for r in rows}
    for who, n in sorted(scores.items(), key=lambda item: -item[1]):
        if who not in recorders:
            lines.append(f"<@{who}> — **{n}** score{'s' if n != 1 else ''}")
    return lines


class Logs(commands.Cog):
    """Logging"""

    def __init__(self, bot):
        self.bot = bot
        self.last_flush = datetime.now(timezone.utc)
        self.announced = False

    async def cog_load(self):
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        """Silence is how a dead bot looks, so say when it comes back."""
        if self.announced:
            return
        self.announced = True
        channel = await self.bot.channel_for("problems_channel")
        if channel is None:
            return
        try:
            await channel.send(embed=discord.Embed(
                description=f"🟢 **{self.bot.brand.name} bot started** — "
                            f"watching for submissions again.",
                color=0x22C55E, timestamp=discord.utils.utcnow()))
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------ loop
    @tasks.loop(minutes=1)
    async def tick(self):
        try:
            await self.flush_activity()
            await self.maybe_brief()
            await self.maybe_prune()
            await self.maybe_backup()
        except Exception:
            log.exception("log housekeeping failed")

    @tick.before_loop
    async def before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ digest
    async def flush_activity(self, force: bool = False) -> None:
        buffer = self.bot.activity_buffer
        if not buffer:
            self.last_flush = datetime.now(timezone.utc)
            return
        every = int(await self.bot.setting("digest_minutes") or 15)
        age = (datetime.now(timezone.utc) - self.last_flush).total_seconds() / 60
        if not force and age < every and len(buffer) < 40:
            return

        entries, buffer[:] = list(buffer), []
        self.last_flush = datetime.now(timezone.utc)
        channel = await self.bot.channel_for("activity_channel")
        if channel is None:
            return

        counts: dict[str, int] = {}
        for action, _ in entries:
            counts[action] = counts.get(action, 0) + 1
        headline = " · ".join(f"**{n}** {action}{'s' if n != 1 else ''}"
                              for action, n in counts.items())

        e = discord.Embed(
            title=f"📋  Last {int(max(age, 1))} minutes",
            description=headline,
            color=self.bot.brand.color,
            timestamp=discord.utils.utcnow())
        e.add_field(
            name="​",
            value=embeds.lines_within([f"• {detail}" for _, detail in entries]),
            inline=False)
        e.set_footer(text=self.bot.brand.name)
        try:
            await channel.send(embed=e)
        except discord.HTTPException:
            log.warning("could not write the activity digest")

    # ------------------------------------------------------------------ briefing
    async def maybe_brief(self) -> None:
        now = datetime.now(timezone.utc)
        if now.hour != int(await self.bot.setting("briefing_hour") or 8):
            return
        stamp = now.strftime("%Y-%m-%d")
        if await self.bot.db.get_setting("_last_briefing") == stamp:
            return
        channel = await self.bot.channel_for("problems_channel", "audit_channel")
        if channel is None:
            return
        await self.bot.db.set_setting("_last_briefing", stamp)
        try:
            await channel.send(embed=await self.briefing())
        except discord.HTTPException:
            log.warning("could not post the morning briefing")

    async def briefing(self) -> discord.Embed:
        """What the two of you should look at today, and nothing else."""
        db = self.bot.db
        counts = await reviews.counts_by_kind(db)
        total = sum(counts.values())
        oldest = await db.val(
            "SELECT created_at FROM requests WHERE status = 'pending' ORDER BY id LIMIT 1")
        unmapped = await db.val(
            "SELECT COUNT(*) FROM achievements WHERE key NOT IN "
            "(SELECT value FROM role_map WHERE kind = 'achievement')", (), 0)
        unlinked = await db.val(
            "SELECT COUNT(*) FROM members WHERE active = 1 AND discord_id IS NULL", (), 0)
        yesterday = await db.val(
            "SELECT COUNT(*) FROM audit_log WHERE ts >= datetime('now', '-1 day')", (), 0)

        e = embeds.base(self.bot.brand, "☀️  Morning briefing")
        if total:
            e.add_field(
                name=f"📥 {total} waiting on you",
                value="\n".join(
                    f"{reviews.KIND_EMOJI[k]} **{counts[k]}** {reviews.KIND_LABEL[k].lower()}"
                    for k in reviews.KINDS if counts.get(k))
                + (f"\n_oldest since {oldest}_" if oldest else "")
                + "\n\nClear them with `/queue`.",
                inline=False)
        else:
            e.add_field(name="📥 Queue", value="✅ Empty — nothing needs you.", inline=False)

        chores = []
        if unmapped:
            chores.append(f"🔗 **{unmapped}** achievements have no Discord role (`/map missing`)")
        if unlinked:
            chores.append(f"👤 **{unlinked}** roster entries aren't linked (`/member match`)")
        if chores:
            e.add_field(name="Worth doing when you have a minute",
                        value="\n".join(chores), inline=False)

        e.add_field(name="Yesterday", value=f"**{yesterday}** recorded changes", inline=False)
        if datetime.now(timezone.utc).weekday() == 0:
            week = await recorder_week(db)
            e.add_field(name="🧾 Who recorded what this week",
                        value=embeds.lines_within(week) if week else "Nobody recorded anything.",
                        inline=False)
        e.set_footer(text=f"{self.bot.brand.name} • posted once a day")
        return e

    # ------------------------------------------------------------------ backups
    async def maybe_backup(self) -> None:
        """A full copy of the database every day, the last two weeks of them kept."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if await self.bot.db.get_setting("_last_backup") == stamp:
            return
        await self.bot.db.set_setting("_last_backup", stamp)
        try:
            await self.bot.db.backup("daily", keep=14)
        except (sqlite3.Error, OSError) as exc:
            await self.bot.problem("The daily backup failed",
                                   f"{exc}. Check there's disk space next to the database.")

    # ------------------------------------------------------------------ retention
    async def maybe_prune(self) -> None:
        days = int(await self.bot.setting("audit_days") or 0)
        if days <= 0:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if await self.bot.db.get_setting("_last_prune") == stamp:
            return
        await self.bot.db.set_setting("_last_prune", stamp)
        cur = await self.bot.db.run(
            "DELETE FROM audit_log WHERE undone = 0 "
            "AND ts < datetime('now', ?)", (f"-{days} days",))
        if cur.rowcount:
            log.info("pruned %s audit entries older than %s days", cur.rowcount, days)
        await self.bot.db.run(
            "DELETE FROM requests WHERE status <> 'pending' "
            "AND created_at < datetime('now', ?)", (f"-{days} days",))


async def setup(bot):
    await bot.add_cog(Logs(bot))
