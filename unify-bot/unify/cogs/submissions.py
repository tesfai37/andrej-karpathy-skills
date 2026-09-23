"""Reads the submissions channel and turns posts into recorded clears.

    @BUDMAN008 dps @vSS Ice HM @vSS Fire HM

Nothing here writes guild data on a member's say-so. A post either meets every
test in reviews.route - recorded by a trusted recorder, for somebody else, with
every role recognised - or it becomes a request an officer approves."""
from __future__ import annotations

import json

import discord
from discord.ext import commands

from .. import embeds, parsing, reviews, store
from ..checks import member_is_recorder, user_is_admin
from ..config import ROLE_EMOJI, ROLE_LABEL
from .mapping import MapRoleView
from .reviewqueue import ReviewCard, card


class FixMappingView(discord.ui.View):
    """Offered when a post mentions a role the bot doesn't recognise."""

    def __init__(self, bot, role_ids: list[int]):
        super().__init__(timeout=900)
        self.bot = bot
        self.role_ids = role_ids

    @discord.ui.button(label="Map these roles", emoji="🔗", style=discord.ButtonStyle.primary)
    async def fix(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await user_is_admin(interaction):
            return await interaction.response.send_message(
                embed=embeds.error("An officer needs to do this one."), ephemeral=True)
        role = interaction.guild.get_role(self.role_ids[0])
        if role is None:
            return await interaction.response.send_message(
                embed=embeds.error("That role no longer exists."), ephemeral=True)
        view = MapRoleView(self.bot, interaction.user.id, role)
        await interaction.response.send_message(embed=await view.build(), view=view,
                                                ephemeral=True)


class Submissions(commands.Cog):
    """Automatic channel reader"""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        channel_id = await self.bot.setting("submissions_channel")
        if not channel_id or message.channel.id != int(channel_id):
            return
        await self.handle(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Somebody fixing a wrong mention shouldn't have to repost."""
        channel_id = await self.bot.setting("submissions_channel")
        if (after.author.bot or after.guild is None or not channel_id
                or after.channel.id != int(channel_id) or before.content == after.content):
            return
        if await self.bot.db.val(
                "SELECT 1 FROM requests WHERE source_url = ? AND status <> 'pending'",
                (after.jump_url,)):
            return          # already decided; an edit can't rewrite history
        await self.bot.db.run(
            "UPDATE requests SET status = 'rejected', note = 'superseded by an edit' "
            "WHERE source_url = ? AND status = 'pending'", (after.jump_url,))
        await self.handle(after)

    # ------------------------------------------------------------------ the flow
    async def handle(self, message: discord.Message) -> None:
        parsed = await parsing.parse_message(self.bot.db, message.content)
        if parsed.is_empty:
            return

        member = await self.identify(parsed)
        role = parsed.roles[0] if parsed.roles else ""

        if parsed.unmapped_role_ids:
            await self.park(message, parsed, member, role)
            return

        if member is None:
            await message.add_reaction("❓")
            await message.reply(
                embed=embeds.warn(
                    "I couldn't tell who this is for. @mention them, or type their exact "
                    "gamertag. If they're new, an officer can add them with `/member add`."),
                mention_author=False)
            await self.bot.problem(
                "Submission with no recognisable member",
                f"{message.author.mention} in {message.channel.mention} — "
                f"[jump]({message.jump_url})")
            return

        if role and await reviews.already_recorded(self.bot.db, member.id, role, parsed.keys):
            await message.add_reaction("♻️")
            await message.reply(
                embed=embeds.info(self.bot.brand,
                                  f"**{member.gamertag}** already has all of this on "
                                  f"{ROLE_LABEL[role]}. Nothing to do."),
                mention_author=False)
            return

        payload = {"keys": parsed.keys, "content": message.content}
        waiting = await reviews.duplicate_request(
            self.bot.db, "achievements", member.id, role, payload)
        if waiting:
            await message.add_reaction("♻️")
            return

        mode = await self.bot.setting("submission_mode")
        routing = reviews.decide(
            mode,
            is_recorder=await member_is_recorder(self.bot, message.guild, message.author.id),
            has_role=bool(role),
            submitter_id=message.author.id,
            subject_discord_id=member.discord_id,
            member_known=True,
            all_roles_mapped=True,
        )

        if routing.auto:
            added = await self.record(member, role, parsed.keys, message.author,
                                      message.guild)
            await message.add_reaction("✅")
            await message.reply(
                embed=embeds.success(
                    f"**{member.gamertag}** — {ROLE_EMOJI[role]} {ROLE_LABEL[role]}\n"
                    + (", ".join(added) if added else "_already had all of these_")),
                mention_author=False)
            return

        await self.queue(message, member, role, payload, routing.note)

    async def park(self, message, parsed, member, role) -> None:
        """A post naming a role the bot doesn't know. Kept, not dropped, so that
        mapping the role later can replay it."""
        names = ", ".join(f"<@&{r}>" for r in parsed.unmapped_role_ids)
        await message.add_reaction("❓")
        request_id = await reviews.open_request(
            self.bot.db, "achievements",
            requester_id=message.author.id,
            member_id=member.id if member else None,
            role=role,
            payload={"keys": parsed.keys, "content": message.content,
                     "unmapped": parsed.unmapped_role_ids},
            source_url=message.jump_url,
            note="mentions a role I don't recognise",
        )
        await message.reply(
            embed=embeds.warn(
                f"I don't know what {names} means yet, so I've held this as request "
                f"`#{request_id}`.\nAn officer can map it below — everything waiting on "
                "that role gets applied automatically."),
            view=FixMappingView(self.bot, parsed.unmapped_role_ids),
            mention_author=False)
        await self.bot.problem(
            "Unrecognised role in a submission",
            f"{names} — request `#{request_id}` is waiting. `/map role` fixes it.")

    async def queue(self, message, member, role, payload, note) -> None:
        request_id = await reviews.open_request(
            self.bot.db, "achievements",
            requester_id=message.author.id, member_id=member.id, role=role,
            payload=payload, source_url=message.jump_url, note=note)
        row = await reviews.get(self.bot.db, request_id)
        posted = await message.reply(embed=await card(self.bot, row),
                                     view=ReviewCard(self.bot), mention_author=False)
        await reviews.attach_card(self.bot.db, request_id, posted.id, posted.channel.id)
        await message.add_reaction("⏳")

    # ------------------------------------------------------------------ helpers
    async def identify(self, parsed: parsing.Parsed) -> store.Member | None:
        """A @mention is the final answer. Only when the post names nobody do we
        fall back to matching a word, so a stray word cannot redirect a clear."""
        db = self.bot.db
        if parsed.discord_ids:
            for discord_id in parsed.discord_ids:
                found = await store.find_member(db, discord_id)
                if found:
                    return found
            return None
        for word in parsed.leftover_words:
            found = await store.find_member(db, word)
            if found:
                return found
        return None

    async def record(self, member, role, keys, actor, guild) -> list[str]:
        added = await store.grant(self.bot.db, member, role, keys, actor.id, str(actor))
        if added:
            await self.bot.activity(
                "clear recorded",
                f"**{member.gamertag}** {ROLE_LABEL[role]} · {', '.join(added)}")
            await self.bot.sync_roles(guild, member, f"earned {', '.join(added)}")
            await self.notify(member, role, added)
        return added

    async def notify(self, member: store.Member, role: str, added: list[str]) -> None:
        if not added or not await self.bot.setting("notify_dm") or not member.discord_id:
            return
        user = self.bot.get_user(member.discord_id)
        if user is None:
            return
        names = await self.bot.db.all(
            f"SELECT name FROM achievements WHERE key IN ({','.join('?' * len(added))})",
            list(added))
        e = embeds.base(
            self.bot.brand, "🎉  Nice clear!",
            f"Recorded on your **{ROLE_LABEL.get(role, role)}** record:\n"
            + "\n".join(f"• {n[0]}" for n in names)
            + "\n\nRun `/me` in the server to see your card.",
        )
        try:
            await user.send(embed=e)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------ replay
    async def replay_role(self, guild, role_id: int, actor) -> int:
        """Called after a role is mapped: re-read every post that was parked on it."""
        rows = await self.bot.db.all(
            "SELECT * FROM requests WHERE status = 'pending' AND kind = 'achievements' "
            "AND note = 'mentions a role I don''t recognise'")
        replayed = 0
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            if role_id not in payload.get("unmapped", []):
                continue
            parsed = await parsing.parse_message(self.bot.db, payload.get("content", ""))
            if parsed.unmapped_role_ids:
                await self.bot.db.run(
                    "UPDATE requests SET payload = ? WHERE id = ?",
                    (json.dumps({**payload, "keys": parsed.keys,
                                 "unmapped": parsed.unmapped_role_ids}), row["id"]))
                continue
            await self.bot.db.run(
                "UPDATE requests SET payload = ?, note = ? WHERE id = ?",
                (json.dumps({**payload, "keys": parsed.keys, "unmapped": []}),
                 "was waiting on a role mapping", row["id"]))
            replayed += 1
        return replayed


async def setup(bot):
    await bot.add_cog(Submissions(bot))
