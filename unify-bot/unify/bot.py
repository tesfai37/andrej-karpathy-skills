"""Bot object: owns the database handle, the brand, and the audit relay."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from . import embeds, rolesync
from .checks import NotAllowed
from .config import Env, SETTING_DEFAULTS
from .db import Database

log = logging.getLogger("unify")

COGS = (
    "unify.cogs.profiles",
    "unify.cogs.find",
    "unify.cogs.guides",
    "unify.cogs.leaderboards",
    "unify.cogs.mapping",
    "unify.cogs.sync",
    "unify.cogs.submissions",
    "unify.cogs.reviewqueue",
    "unify.cogs.membership",
    "unify.cogs.logs",
    "unify.cogs.admin",
    "unify.cogs.dataio",
    "unify.cogs.settings",
    "unify.cogs.helpmenu",
    "unify.cogs.reports",
    "unify.cogs.welcome",
    "unify.cogs.oldcommands",
)


class UnifyBot(commands.Bot):
    def __init__(self, env: Env):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!!unused", intents=intents, help_command=None)
        self.env = env
        self.db = Database(env.db_path)
        self.brand = embeds.Brand()
        # Routine recordings are batched into a digest rather than streamed one
        # message at a time; the logs cog drains this.
        self.activity_buffer: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ setup
    async def setup_hook(self) -> None:
        await self.db.connect()
        catalog = json.loads(
            (Path(__file__).parent / "data" / "catalog.json").read_text(encoding="utf-8")
        )
        await self.db.seed_catalog(catalog)
        await self.refresh_brand()

        for cog in COGS:
            await self.load_extension(cog)

        self.tree.on_error = self.on_tree_error
        self.restrict_to_guilds()

        if self.env.guild_id:
            guild = discord.Object(id=self.env.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        log.info("commands synced")

    def restrict_to_guilds(self) -> None:
        """Every command reads guild state - roles, members, channels. Without
        this a global sync makes them all invocable in a DM, where
        interaction.guild is None and they raise instead of answering.

        NB: app_commands.AppCommandContext, not discord.AppCommandContext - the
        latter is an unrelated flags class of the same name that the payload
        builder raises on."""
        guild_only = app_commands.AppCommandContext(
            guild=True, dm_channel=False, private_channel=False)
        for command in self.tree.get_commands():
            command.allowed_contexts = guild_only

    async def close(self) -> None:
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="/help")
        )

    # ------------------------------------------------------------------ helpers
    async def setting(self, key: str):
        return await self.db.get_setting(key, SETTING_DEFAULTS[key][1])

    async def refresh_brand(self) -> None:
        self.brand = embeds.Brand.from_settings(
            await self.setting("guild_name"),
            await self.setting("brand_color"),
            await self.setting("logo_url"),
        )

    async def sync_roles(self, guild: discord.Guild | None, member, reason: str) -> None:
        """Give (or take back) the Discord roles that match somebody's record.
        Quietly does nothing unless an admin has turned role_sync on."""
        if guild is None or not await self.setting("role_sync"):
            return
        plan = await rolesync.plan_member(self.db, guild, member)
        if plan.changes:
            problem = await rolesync.apply_plan(guild, plan, reason)
            if problem:
                log.warning("role sync for %s: %s", member.gamertag, problem)

    async def channel_for(self, setting: str, fallback: str = "") -> discord.abc.Messageable | None:
        for key in (setting, fallback):
            if not key:
                continue
            channel_id = await self.setting(key)
            if channel_id:
                channel = self.get_channel(int(channel_id))
                if channel is not None:
                    return channel
        return None

    async def activity(self, action: str, detail: str) -> None:
        """Routine, high-volume, and only interesting in aggregate."""
        self.activity_buffer.append((action, detail))

    async def problem(self, title: str, detail: str) -> None:
        """Something a human has to look at. Goes out immediately, and is the only
        log an officer should ever need to watch."""
        log.warning("%s: %s", title, detail)
        channel = await self.channel_for("problems_channel", "audit_channel")
        if channel is None:
            return
        e = discord.Embed(title=f"⚠️  {title}", description=detail[:4000],
                          color=0xF59E0B, timestamp=discord.utils.utcnow())
        try:
            await channel.send(embed=e)
        except discord.HTTPException:
            log.warning("could not write to the problems channel")

    async def audit(self, actor: discord.abc.User, action: str, summary: str,
                    entry_id: int | None = None) -> None:
        """Mirror an admin action into the audit channel, if one is configured."""
        channel = await self.channel_for("audit_channel")
        if channel is None:
            return
        e = discord.Embed(
            description=f"**{action}** — {summary}",
            color=self.brand.color,
            timestamp=discord.utils.utcnow(),
        )
        e.set_author(name=str(actor), icon_url=getattr(actor.display_avatar, "url", None))
        if entry_id:
            e.set_footer(text=f"audit #{entry_id} • /undo {entry_id} to reverse")
        try:
            await channel.send(embed=e)
        except discord.HTTPException:
            log.warning("could not write to the audit channel")

    async def on_tree_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if isinstance(error, NotAllowed):
            message = error.message
        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            message = f"Slow down a moment — try again in {error.retry_after:.0f}s."
        else:
            log.exception("command error", exc_info=error)
            command = getattr(interaction.command, "qualified_name", "unknown")
            await self.problem(
                f"/{command} failed",
                f"For {interaction.user.mention}.\n```\n{type(error).__name__}: "
                f"{str(error)[:600]}\n```")
            message = ("Something went wrong on my end. I've logged it for the officers; "
                       "nothing was changed.")
        embed = embeds.error(message)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass
