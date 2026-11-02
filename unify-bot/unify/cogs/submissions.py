"""Reads the submissions channel and turns posts into recorded clears.

    @inkeddragonmami healer @vCR Gryphon Heart          one person

    @vSS HM @Godslayer                                   a whole run
    score: 257,498
    tank: @a @b
    healer: @c @d
    dps: @e @f @g @h @i @j @k @l

Nothing here writes guild data on a member's say-so. Every person in a post is
routed on their own through reviews.decide: recorded straight away only when a
trusted recorder posts it for somebody else, with every role recognised and a
screenshot attached, in a post that hasn't been edited. Everything else is
gathered into one request an officer approves. A run's score is one number for
everybody in it, applied straight away only when the poster wasn't in the run
and it isn't a new guild best.

Every instant recording leaves the bot's own copy of its screenshot behind, linked
from the audit log, and about one in twenty goes to an officer as a spot check.

Nobody mentioned in a post is ever dropped without saying so."""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field

import discord
from discord.ext import commands

from .. import embeds, parsing, reviews, store
from ..checks import member_is_recorder, user_is_admin
from ..config import ROLE_EMOJI, ROLE_LABEL
from .mapping import MapRoleView
from .oldcommands import translate
from .reviewqueue import copy_of, post_card, usable_proof

JUMP_RE = re.compile(r"/channels/(\d+)/(\d+)/(\d+)")
SPOT_CHECK_RATE = 1 / 20      # share of instant recordings an officer looks at afterwards


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
        # The two ways a trusted recorder could slip in somebody who wasn't there and
        # leave nothing to check: editing a post after it's recorded, and posting
        # without a screenshot. Neither ever records straight away.
        held = ("added by an edit, so an officer checks it" if message.edited_at is not None
                else "no screenshot attached, so it can't record straight away"
                if proof is None else "")
        instant: list[tuple[store.Member, str]] = []
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
                if routing.auto and held:
                    routing = reviews.Routing(False, held)
                if routing.auto:
                    instant.append((member, role))
                else:
                    review.append([member.id, role])
                    out.waiting.append((member, role))
                    notes.add(routing.note)

        score_now, score_payload = await self.score(message, parsed, people, recorder, mode,
                                                    held, out, notes)

        evidence = ""
        if instant or score_now:
            evidence = await self.keep_evidence(message, parsed, proof, instant, score_now)
            if not evidence:
                # Nowhere to keep the copy, so nothing records straight away.
                notes.add("there's nowhere to keep a copy of the screenshot")
                for member, role in instant:
                    review.append([member.id, role])
                    out.waiting.append((member, role))
                instant = []
                if score_now:
                    score_now, score_payload = False, self.score_request(parsed, people)
                    out.score_line = f"🏆 {self.score_label(parsed)} — waiting for an officer"

        for member, role in instant:
            added = await self.record(member, role, parsed.keys, message.author,
                                      message.guild, evidence)
            out.recorded.append((member, role, added))
        raised = await self.apply_score(message, parsed, people, out, evidence) if score_now else []

        if review or score_payload:
            payload = {"keys": parsed.keys, "members": review, **(score_payload or {})}
            out.duplicate_of = await reviews.duplicate_request(db, "run", None, "", payload)
            if out.duplicate_of is None:
                payload["content"] = message.content
                out.request_id = await reviews.open_request(
                    db, "run", requester_id=message.author.id, payload=payload,
                    proof_url=proof.url if proof else "", source_url=message.jump_url,
                    note="; ".join(sorted(notes)))

        await self.report(message, parsed, out, proof, evidence)
        if evidence and random.random() < SPOT_CHECK_RATE:
            await self.spot_check(message, parsed, proof, out, raised, evidence)

    @staticmethod
    def score_label(parsed: parsing.Parsed) -> str:
        return f"{parsed.score_trial} score **{parsed.score:,}**"

    @staticmethod
    def score_request(parsed: parsing.Parsed, people) -> dict:
        return {"score": parsed.score, "trial": parsed.score_trial,
                "score_members": [m.id for m, _ in people]}

    async def score(self, message, parsed, people, recorder, mode, held, out, notes):
        """What happens to a run's score: (apply it now?, the request payload if not).

        A run's score is one number for everybody in it. It applies straight away
        only when a recorder posts it, with a screenshot, for a run they weren't in,
        and it isn't a new guild best: a typo there would top the leaderboard."""
        if parsed.score is None:
            return False, None
        if parsed.score_note or not parsed.score or not parsed.score_trial:
            out.score_ok = False
            out.score_line = (f"⚠️ score not recorded — {parsed.score_note or 'unreadable'}. "
                              "Post it as e.g. `vss score 257,498`.")
            return False, None
        if not people:
            return False, None
        in_run = any(m.discord_id == message.author.id for m, _ in people)
        best = await self.bot.db.val("SELECT MAX(score) FROM scores WHERE trial_key = ?",
                                     (parsed.score_trial,))
        guild_best = best is None or parsed.score > best
        if recorder and not in_run and mode != "review" and not held and not guild_best:
            return True, None
        notes.add("the score is for a run you were in" if in_run
                  else "every submission is reviewed here" if mode == "review"
                  else "submitted by someone who isn't a recorder" if not recorder
                  else held or f"a new guild best for {parsed.score_trial}, so an officer "
                               "checks it")
        out.score_line = f"🏆 {self.score_label(parsed)} — waiting for an officer"
        return False, self.score_request(parsed, people)

    async def apply_score(self, message, parsed, people, out, evidence) -> list[store.Member]:
        """Give the run's score to everyone in it who doesn't already have better."""
        raised, kept = [], 0
        for member, _ in people:
            if await store.raise_score(self.bot.db, member, parsed.score_trial, parsed.score,
                                       message.author.id, str(message.author), evidence):
                raised.append(member)
                await self.notify_score(member, parsed.score_trial, parsed.score)
            else:
                kept += 1
        out.score_line = (f"🏆 {self.score_label(parsed)} for {len(raised)}"
                          + (f" · {kept} already had a better one" if kept else ""))
        out.score_recorded = True
        return raised

    async def keep_evidence(self, message, parsed, proof, instant, score_now) -> str:
        """The bot's own copy of the screenshot behind an instant recording, so the
        evidence survives the post being edited or deleted. Returns a link to the
        copy, or "" when there's nowhere to keep one."""
        channel = await self.bot.channel_for("evidence_channel", "audit_channel")
        file = await copy_of(proof, "evidence") if channel is not None else None
        if file is None:
            return ""
        e = discord.Embed(
            title="📎  Evidence",
            description=f"{message.author.mention} recorded this straight away from "
                        f"[their post]({message.jump_url}).",
            color=self.bot.brand.color, timestamp=discord.utils.utcnow())
        if instant:
            e.add_field(name="Recorded", value=embeds.lines_within([", ".join(
                f"{m.gamertag} {ROLE_EMOJI.get(r, '')}" for m, r in instant)]), inline=False)
        if parsed.keys:
            e.add_field(name="Achievements", value=", ".join(parsed.keys), inline=True)
        if score_now:
            e.add_field(name="Score", value=self.score_label(parsed), inline=True)
        e.set_image(url=f"attachment://{file.filename}")
        try:
            sent = await channel.send(embed=e, file=file)
        except discord.HTTPException:
            return ""
        return sent.jump_url

    async def spot_check(self, message, parsed, proof, out, raised, evidence) -> None:
        """About one instant recording in twenty goes to an officer afterwards. It
        costs a few clicks a week, and no recorder can count on going unseen."""
        recorded = [[m.id, r, added] for m, r, added in out.recorded if added]
        if not recorded and not raised:
            return
        payload = {"evidence": evidence, "recorded": recorded, "keys": parsed.keys}
        if raised:
            payload.update(score=parsed.score, trial=parsed.score_trial,
                           score_raised=[m.id for m in raised])
        request_id = await reviews.open_request(
            self.bot.db, "check", requester_id=message.author.id, payload=payload,
            proof_url=proof.url, source_url=message.jump_url, note="a random spot check")
        channel = await self.bot.channel_for("audit_channel", "problems_channel")
        if channel is not None:
            await post_card(self.bot, await reviews.get(self.bot.db, request_id),
                            proof=proof, channel=channel)

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
    async def report(self, message, parsed, out: Outcome, proof, evidence: str = "") -> None:
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
                + f" · {', '.join(parsed.keys)}"
                + (f" · [evidence]({evidence})" if evidence else ""))
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
    async def record(self, member, role, keys, actor, guild, evidence: str = "") -> list[str]:
        added = await store.grant(self.bot.db, member, role, keys, actor.id, str(actor),
                                  evidence=evidence)
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
