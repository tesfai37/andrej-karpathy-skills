"""Reads the submissions channel and turns posts into recorded clears.

    @inkeddragonmami healer @vCR Gryphon Heart          one person

    @vSS HM @Godslayer                                   a whole run
    score: 257,498
    tank: @a @b
    healer: @c @d
    dps: @e @f @g @h @i @j @k @l

Nothing here writes guild data on a member's say-so. Every person in a post is
routed on their own through reviews.decide: recorded straight away only when a
trusted recorder posts it for somebody else with every role recognised, and
otherwise gathered into one request an officer approves. A run's score is one
number for everybody in it, and is applied automatically only when the person
posting it wasn't in the run.

Nobody mentioned in a post is ever dropped without saying so."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import discord
from discord.ext import commands

from .. import embeds, parsing, reviews, store
from ..checks import member_is_recorder, user_is_admin
from ..config import ROLE_EMOJI, ROLE_LABEL
from .mapping import MapRoleView
from .oldcommands import translate
from .reviewqueue import post_card, usable_proof

JUMP_RE = re.compile(r"/channels/(\d+)/(\d+)/(\d+)")


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


@dataclass
class Outcome:
    """What happened to everybody in one post."""
    recorded: list[tuple[store.Member, str, list[str]]] = field(default_factory=list)
    already: list[store.Member] = field(default_factory=list)
    waiting: list[tuple[store.Member, str]] = field(default_factory=list)
    unknown: list[int] = field(default_factory=list)
    request_id: int | None = None
    duplicate_of: int | None = None
    score_line: str = ""          # what happened to the run's score, if one was given
    score_ok: bool = True         # False when a score was given but couldn't be used
    score_recorded: bool = False


class Submissions(commands.Cog):
    """Automatic channel reader"""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ listeners
    async def watched(self, message: discord.Message) -> bool:
        if message.author.bot or message.guild is None:
            return False
        if translate(message.content) is not None:
            return False        # an old ! command: the translator answers it, nothing is read
        channel_id = await self.bot.setting("submissions_channel")
        return bool(channel_id) and message.channel.id == int(channel_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if await self.watched(message):
            await self.handle(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Somebody fixing a wrong mention shouldn't have to repost. An edit can add
        people; it can't take back anything already recorded or decided."""
        if before.content == after.content or not await self.watched(after):
            return
        if await self.bot.db.val(
                "SELECT 1 FROM requests WHERE source_url = ? "
                "AND status IN ('approved', 'rejected')", (after.jump_url,)):
            return
        await self.bot.db.run(
            "UPDATE requests SET status = 'superseded', note = 'replaced by an edit' "
            "WHERE source_url = ? AND status = 'pending'", (after.jump_url,))
        await self.handle(after)

    # ------------------------------------------------------------------ the flow
    async def handle(self, message: discord.Message) -> None:
        db = self.bot.db
        parsed = await parsing.parse_message(db, message.content)
        if parsed.is_empty:
            return
        proof = usable_proof(message.attachments)

        if parsed.unmapped_role_ids:
            await self.park(message, parsed, proof)
            return

        people, unknown = await self.people(parsed)
        if not people and not unknown:
            await message.add_reaction("❓")
            await message.reply(
                embed=embeds.warn(
                    "I couldn't tell who this is for. @mention them, or type their exact "
                    "gamertag. If they're new, they can run `/register`."),
                mention_author=False)
            await self.bot.problem(
                "Submission with no recognisable member",
                f"{message.author.mention} in {message.channel.mention} — "
                f"[jump]({message.jump_url})")
            return

        out = Outcome(unknown=unknown)
        mode = await self.bot.setting("submission_mode")
        recorder = await member_is_recorder(self.bot, message.guild, message.author.id)
        review: list[list] = []
        notes: set[str] = set()

        # Each person is routed on their own. A post with only a score has no
        # clears to route, so it goes straight to the score.
        if parsed.keys:
            for member, role in people:
                if role and await reviews.already_recorded(db, member.id, role, parsed.keys):
                    out.already.append(member)
                    continue
                routing = reviews.decide(
                    mode, is_recorder=recorder, has_role=bool(role),
                    submitter_id=message.author.id, subject_discord_id=member.discord_id,
                    member_known=True, all_roles_mapped=True)
                if routing.auto:
                    added = await self.record(member, role, parsed.keys, message.author,
                                              message.guild)
                    out.recorded.append((member, role, added))
                else:
                    review.append([member.id, role])
                    out.waiting.append((member, role))
                    notes.add(routing.note)

        score_payload = await self.score(message, parsed, people, recorder, mode, out, notes)

        if review or score_payload:
            payload = {"keys": parsed.keys, "members": review, **(score_payload or {})}
            out.duplicate_of = await reviews.duplicate_request(db, "run", None, "", payload)
            if out.duplicate_of is None:
                payload["content"] = message.content
                out.request_id = await reviews.open_request(
                    db, "run", requester_id=message.author.id, payload=payload,
                    proof_url=proof.url if proof else "", source_url=message.jump_url,
                    note="; ".join(sorted(notes)))

        await self.report(message, parsed, out, proof)

    async def score(self, message, parsed, people, recorder, mode, out, notes) -> dict | None:
        """A run's score is one number for everybody in the run. It's applied here
        only when a recorder posts it for a run they weren't in; otherwise it rides
        along on the request for an officer."""
        if parsed.score is None:
            return None
        if parsed.score_note or not parsed.score or not parsed.score_trial:
            out.score_ok = False
            out.score_line = (f"⚠️ score not recorded — {parsed.score_note or 'unreadable'}. "
                              "Post it as e.g. `vss score 257,498`.")
            return None
        everyone = [m for m, _ in people]
        if not everyone:
            return None
        in_run = any(m.discord_id == message.author.id for m in everyone)
        label = f"{parsed.score_trial} score **{parsed.score:,}**"

        if recorder and not in_run and mode != "review":
            raised = kept = 0
            for member in everyone:
                if await store.raise_score(self.bot.db, member, parsed.score_trial,
                                           parsed.score, message.author.id,
                                           str(message.author)):
                    raised += 1
                    await self.notify_score(member, parsed.score_trial, parsed.score)
                else:
                    kept += 1
            out.score_line = (f"🏆 {label} for {raised}"
                              + (f" · {kept} already had a better one" if kept else ""))
            out.score_recorded = True
            return None

        notes.add("the score is for a run you were in" if in_run
                  else "every submission is reviewed here" if mode == "review"
                  else "submitted by someone who isn't a recorder")
        out.score_line = f"🏆 {label} — waiting for an officer"
        return {"score": parsed.score, "trial": parsed.score_trial,
                "score_members": [m.id for m in everyone]}

    async def people(self, parsed: parsing.Parsed) -> tuple[list, list[int]]:
        """Everybody the post is for, each with their role.

        A @mention is the final answer, looked up exactly. Only when a post names
        nobody does it fall back to an exact gamertag in the text, so a stray word
        can never redirect a clear onto someone else."""
        db = self.bot.db
        found, unknown = [], []
        if parsed.discord_ids:
            for discord_id, role in parsed.assignments:
                member = await store.find_member(db, discord_id)
                if member is None:
                    unknown.append(discord_id)
                else:
                    found.append((member, role))
            return found, unknown
        only_role = parsed.roles[0] if len(parsed.roles) == 1 else ""
        for word in parsed.leftover_words:
            member = await store.find_member(db, word)
            if member is not None:
                return [(member, only_role)], []
        return [], []

    # ------------------------------------------------------------------ telling people
    async def report(self, message, parsed, out: Outcome, proof) -> None:
        """One summary reply, plus a card when something needs an officer."""
        lines = []
        if out.recorded:
            lines.append(f"✅ Recorded for {len(out.recorded)}: " + ", ".join(
                f"{m.gamertag} {ROLE_EMOJI.get(r, '')}" for m, r, _ in out.recorded))
        if out.waiting:
            lines.append("⏳ Waiting for an officer: "
                         + ", ".join(m.gamertag for m, _ in out.waiting))
        if out.score_line:
            lines.append(out.score_line)
        if out.already:
            lines.append("♻️ Already had it: " + ", ".join(m.gamertag for m in out.already))
        if out.duplicate_of:
            lines.append(f"♻️ The same thing is already waiting as request `#{out.duplicate_of}`.")
        if out.unknown:
            lines.append("❓ Not on the roster, so nothing recorded: "
                         + " ".join(f"<@{d}>" for d in out.unknown)
                         + "\n　They can run `/register`, or an officer can `/member add` them.")

        if out.recorded or out.score_recorded:
            await message.add_reaction("✅")
        if out.request_id:
            await message.add_reaction("⏳")
        if out.unknown or not out.score_ok:
            await message.add_reaction("❓")
        if (out.already or out.duplicate_of) and not (out.recorded or out.request_id):
            await message.add_reaction("♻️")

        only_waiting = out.request_id and not (out.recorded or out.already or out.unknown
                                              or not out.score_ok)
        if lines and not only_waiting:
            await message.reply(embed=discord.Embed(description="\n".join(lines),
                                                    color=self.bot.brand.color),
                                mention_author=False)
        if out.request_id:
            row = await reviews.get(self.bot.db, out.request_id)
            await post_card(self.bot, row, proof=proof, reply_to=message)

        if out.recorded:
            await self.bot.activity(
                "run recorded",
                f"{message.author.display_name}: " + ", ".join(
                    m.gamertag for m, _, _ in out.recorded)
                + f" · {', '.join(parsed.keys)}")
        if out.unknown:
            await self.bot.problem(
                "Submission names people who aren't on the roster",
                " ".join(f"<@{d}>" for d in out.unknown)
                + f" — [jump]({message.jump_url})")

    async def park(self, message, parsed, proof) -> None:
        """A post naming a role the bot doesn't know. Kept, not dropped: mapping the
        role reads the post again from the top."""
        names = ", ".join(f"<@&{r}>" for r in parsed.unmapped_role_ids)
        await message.add_reaction("❓")
        request_id = await reviews.open_request(
            self.bot.db, "run", requester_id=message.author.id,
            payload={"keys": parsed.keys, "members": [], "content": message.content,
                     "unmapped": parsed.unmapped_role_ids},
            proof_url=proof.url if proof else "", source_url=message.jump_url,
            note="mentions a role I don't recognise")
        await message.reply(
            embed=embeds.warn(
                f"I don't know what {names} means yet, so I've held this as request "
                f"`#{request_id}`.\nOnce an officer maps it, I'll read this post again "
                "and record it the normal way."),
            view=FixMappingView(self.bot, parsed.unmapped_role_ids),
            mention_author=False)
        await self.bot.problem(
            "Unrecognised role in a submission",
            f"{names} — request `#{request_id}` is waiting. `/map role` fixes it.")

    # ------------------------------------------------------------------ helpers
    async def record(self, member, role, keys, actor, guild) -> list[str]:
        added = await store.grant(self.bot.db, member, role, keys, actor.id, str(actor))
        if added:
            await self.bot.sync_roles(guild, member, f"earned {', '.join(added)}")
            await self.notify(member, role, added)
        return added

    async def dm(self, member: store.Member, embed: discord.Embed) -> None:
        if not await self.bot.setting("notify_dm") or not member.discord_id:
            return
        user = self.bot.get_user(member.discord_id)
        if user is None:
            return
        try:
            await user.send(embed=embed)
        except discord.HTTPException:
            pass

    async def notify(self, member: store.Member, role: str, added: list[str]) -> None:
        if not added:
            return
        names = await self.bot.db.all(
            f"SELECT name FROM achievements WHERE key IN ({','.join('?' * len(added))})",
            list(added))
        await self.dm(member, embeds.base(
            self.bot.brand, "🎉  Nice clear!",
            f"Recorded on your **{ROLE_LABEL.get(role, role)}** record:\n"
            + "\n".join(f"• {n[0]}" for n in names)
            + "\n\nRun `/me` in the server to see your card."))

    async def notify_score(self, member: store.Member, trial: str, score: int) -> None:
        await self.dm(member, embeds.base(
            self.bot.brand, "🏆  New best score",
            f"Your **{trial}** score is now **{score:,}**.\n\nRun `/me` to see it."))

    # ------------------------------------------------------------------ replay
    async def replay_role(self, guild, role_id: int, actor) -> int:
        """Called after a role is mapped: read every post parked on it again, from
        the top, so it's routed exactly as if the role had been known all along."""
        rows = await self.bot.db.all(
            "SELECT * FROM requests WHERE status = 'pending' AND kind = 'run' "
            "AND note = 'mentions a role I don''t recognise'")
        replayed = 0
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            if role_id not in payload.get("unmapped", []):
                continue
            message = await self.fetch(guild, row["source_url"])
            if message is None:
                await self.bot.db.run(
                    "UPDATE requests SET note = ? WHERE id = ?",
                    ("the role is mapped now, but the original post is gone", row["id"]))
                continue
            await self.bot.db.run(
                "UPDATE requests SET status = 'superseded', note = 'read again once the "
                "role was mapped' WHERE id = ?", (row["id"],))
            await self.handle(message)
            replayed += 1
        return replayed

    async def fetch(self, guild, jump_url: str) -> discord.Message | None:
        match = JUMP_RE.search(jump_url or "")
        if guild is None or match is None:
            return None
        channel = guild.get_channel(int(match.group(2)))
        if channel is None:
            return None
        try:
            return await channel.fetch_message(int(match.group(3)))
        except discord.HTTPException:
            return None


async def setup(bot):
    await bot.add_cog(Submissions(bot))
