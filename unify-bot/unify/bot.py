"""Bot object: owns the database handle, the brand, and the audit relay."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from . import embeds
from .checks import NotAllowed
from .config import Env, SETTING_DEFAULTS
from .db import Database

log = logging.getLogger("unify")

COGS = (
    "unify.cogs.profiles",
    "unify.cogs.leaderboards",
    "unify.cogs.mapping",
    "unify.cogs.submissions",
    "unify.cogs.admin",
    "unify.cogs.dataio",
    "unify.cogs.settings",
    "unify.cogs.helpmenu",
    "unify.cogs.reports",
    "unify.cogs.welcome",
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
        if self.env.guild_id:
            guild = discord.Object(id=self.env.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        log.info("commands synced")

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

    async def audit(self, actor: discord.abc.User, action: str, summary: str,
                    entry_id: int | None = None) -> None:
        """Mirror an admin action into the audit channel, if one is configured."""
        channel_id = await self.setting("audit_channel")
        channel = self.get_channel(int(channel_id)) if channel_id else None
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
            message = ("Something went wrong on my end. An admin can check the bot logs; "
                       "nothing was changed.")
        embed = embeds.error(message)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass
