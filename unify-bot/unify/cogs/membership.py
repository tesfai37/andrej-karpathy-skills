"""What a member is allowed to do for themselves: ask, and look.

/register and /submit raise requests. Neither writes anything. The only way a
row changes is an officer approving it in /queue, so a member who lies achieves
nothing except wasting an officer's click."""
from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, reviews, store
from .reviewqueue import post_card, usable_proof

GAMERTAG = re.compile(r"^[A-Za-z0-9 ._'@\-]{2,40}$")
BAD_PROOF = "The proof needs to be a screenshot: an image under 8 MB."


class Membership(commands.Cog):
    """Members"""

    def __init__(self, bot):
        self.bot = bot

    async def raise_request(self, interaction: discord.Interaction, kind: str,
                            proof: discord.Attachment, **kwargs) -> int:
        """Open a request and put its card, screenshot included, where officers
        will see it. Copying the screenshot can outlast Discord's three seconds,
        so callers defer first."""
        request_id = await reviews.open_request(self.bot.db, kind, proof_url=proof.url, **kwargs)
        row = await reviews.get(self.bot.db, request_id)
        channel = await self.bot.channel_for("audit_channel", "problems_channel")
        if channel is not None:
            await post_card(self.bot, row, proof=proof, channel=channel)
        return request_id

    # ------------------------------------------------------------------ register
    @app_commands.command(description="Ask an officer to link your gamertag to this account")
    @app_commands.describe(gamertag="Exactly as it appears in game",
                           proof="A screenshot of your gamertag in game — required")
    @app_commands.checks.cooldown(2, 600, key=lambda i: i.user.id)
    async def register(self, interaction: discord.Interaction, gamertag: str,
                       proof: discord.Attachment):
        await self.claim(interaction, gamertag, proof)

    async def claim(self, interaction: discord.Interaction, gamertag: str,
                    proof: discord.Attachment | None) -> None:
        """Everything /register does. The welcome card's button lands here too."""
        if not await self.bot.setting("registration"):
            return await interaction.response.send_message(
                embed=embeds.warn("Self-registration is turned off. Ask an officer to add you."),
                ephemeral=True)

        gamertag = gamertag.strip()
        if not GAMERTAG.match(gamertag):
            return await interaction.response.send_message(
                embed=embeds.error(
                    "That doesn't look like a gamertag. Letters, numbers, spaces and "
                    "`. _ ' - @` only."), ephemeral=True)

        mine = await store.find_member(self.bot.db, interaction.user.id)
        if mine is not None:
            return await interaction.response.send_message(
                embed=embeds.warn(
                    f"This account is already **{mine.gamertag}**.\n"
                    "An officer can change that with `/member link` — it isn't "
                    "something you can do yourself."), ephemeral=True)

        waiting = await self.bot.db.val(
            "SELECT id FROM requests WHERE status = 'pending' AND kind = 'register' "
            "AND requester_id = ?", (interaction.user.id,))
        if waiting:
            return await interaction.response.send_message(
                embed=embeds.info(self.bot.brand,
                                  f"You already have claim `#{waiting}` waiting. "
                                  "An officer will get to it."), ephemeral=True)

        existing = await store.find_member(self.bot.db, gamertag)
        if existing is not None and existing.discord_id is not None:
            return await interaction.response.send_message(
                embed=embeds.error(
                    f"**{gamertag}** is already linked to another Discord account. "
                    "If that's wrong, talk to an officer."), ephemeral=True)

        if proof is None or usable_proof([proof]) is None:
            return await interaction.response.send_message(
                embed=embeds.error(BAD_PROOF), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        request_id = await self.raise_request(
            interaction, "register", proof, requester_id=interaction.user.id,
            subject=gamertag,
            note="claims an existing record" if existing else "new name")

        held, _ = await reviews.inherits(self.bot.db, gamertag)
        extra = (f"\n\nBecause **{gamertag}** already has {held} achievements on file, "
                 "an officer will check this one carefully." if held else "")
        await interaction.followup.send(
            embed=embeds.success(
                f"Claim `#{request_id}` raised for **{gamertag}**.\n"
                f"Nothing is linked until an officer approves it.{extra}"),
            ephemeral=True)

    # ------------------------------------------------------------------ submit
    group = app_commands.Group(name="submit",
                               description="Send a score or parse for an officer to check")

    async def submissions_open(self, interaction: discord.Interaction) -> store.Member | None:
        if not await self.bot.setting("member_submissions"):
            await interaction.response.send_message(
                embed=embeds.warn(
                    "Member submissions are turned off. Pass your screenshot to an "
                    "officer and they'll record it."), ephemeral=True)
            return None
        mine = await store.find_member(self.bot.db, interaction.user.id)
        if mine is None:
            await interaction.response.send_message(
                embed=embeds.error(
                    "You're not on the roster yet — run `/register` first."), ephemeral=True)
            return None
        return mine

    @group.command(name="score", description="Send in a trial score")
    @app_commands.describe(trial="Which trial", score="The score",
                           proof="A screenshot showing it — required")
    @app_commands.autocomplete(trial=autocomplete.trials)
    @app_commands.checks.cooldown(5, 600, key=lambda i: i.user.id)
    async def submit_score(self, interaction: discord.Interaction, trial: str,
                           score: app_commands.Range[int, 1, 1_000_000],
                           proof: discord.Attachment):
        mine = await self.submissions_open(interaction)
        if mine is None:
            return
        if not await self.bot.db.one("SELECT 1 FROM trials WHERE key = ?", (trial,)):
            return await interaction.response.send_message(
                embed=embeds.error(f"`{trial}` isn't a trial I know."), ephemeral=True)
        if usable_proof([proof]) is None:
            return await interaction.response.send_message(
                embed=embeds.error(BAD_PROOF), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        request_id = await self.raise_request(
            interaction, "score", proof, requester_id=interaction.user.id, member_id=mine.id,
            payload={"trial": trial, "score": int(score)}, note="sent in by the member")
        await interaction.followup.send(
            embed=embeds.success(
                f"Sent `#{request_id}` for an officer to check: **{trial} = {score:,}**."),
            ephemeral=True)

    @group.command(name="parse", description="Send in a parse")
    @app_commands.describe(label="What you parsed on, e.g. your class",
                           dps="Damage per second", proof="A screenshot showing it — required")
    @app_commands.autocomplete(label=autocomplete.parse_labels)
    @app_commands.checks.cooldown(5, 600, key=lambda i: i.user.id)
    async def submit_parse(self, interaction: discord.Interaction, label: str,
                           dps: app_commands.Range[int, 1, 500_000],
                           proof: discord.Attachment):
        mine = await self.submissions_open(interaction)
        if mine is None:
            return
        if usable_proof([proof]) is None:
            return await interaction.response.send_message(
                embed=embeds.error(BAD_PROOF), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        request_id = await self.raise_request(
            interaction, "parse", proof, requester_id=interaction.user.id, member_id=mine.id,
            payload={"label": label.strip(), "dps": int(dps)}, note="sent in by the member")
        await interaction.followup.send(
            embed=embeds.success(
                f"Sent `#{request_id}` for an officer to check: **{label} = {dps:,}**."),
            ephemeral=True)

    # ------------------------------------------------------------------ leavers
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Somebody leaving shouldn't sit on the roster forever. Deactivated, never
        deleted - their record is still the guild's history."""
        row = await self.bot.db.one(
            "SELECT id, gamertag FROM members WHERE discord_id = ? AND active = 1",
            (member.id,))
        if row is None:
            return
        await self.bot.db.run("UPDATE members SET active = 0 WHERE id = ?", (row["id"],))
        await self.bot.activity("member left",
                                f"**{row['gamertag']}** left the server — deactivated")


async def setup(bot):
    await bot.add_cog(Membership(bot))
