"""Everything waiting on an officer, in one place.

Two ways in: the card the bot posts under a submission, and /queue for clearing
a backlog in batches. Both funnel through the same approve path, so there is
exactly one piece of code that turns a request into guild data.

Every card carries its evidence. A screenshot sent with a submission is copied
onto the card itself rather than linked, because Discord's attachment links
expire and an officer may not get to the card the same day."""
from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import embeds, reviews, store, views
from ..checks import admin_only, user_is_admin
from ..config import ROLE_EMOJI, ROLE_LABEL

log = logging.getLogger("unify.reviews")

APPROVE_ID = "unify:rev:approve"
REJECT_ID = "unify:rev:reject"
ROLE_ID = "unify:rev:role"
PER_PAGE = 8
PROOF_LIMIT = 8 * 1024 * 1024          # comfortably under Discord's upload cap


def usable_proof(attachments) -> discord.Attachment | None:
    """The first image attached, if it's small enough to copy onto a card."""
    for att in attachments or []:
        if (att.content_type or "").startswith("image/") and att.size <= PROOF_LIMIT:
            return att
    return None


async def copy_of(proof: discord.Attachment, stem: str = "proof") -> discord.File | None:
    """A fresh upload of a screenshot. A link to the original would expire."""
    ext = "." + proof.filename.rsplit(".", 1)[-1].lower() if "." in proof.filename else ".png"
    try:
        return await proof.to_file(filename=f"{stem}{ext}")
    except discord.HTTPException:
        log.warning("could not copy the screenshot %s", proof.filename)
        return None


async def run_members(bot, payload: dict) -> list[tuple[store.Member, str]]:
    out = []
    for member_id, role in payload.get("members", []):
        member = await store.get_member(bot.db, member_id)
        if member is not None:
            out.append((member, role))
    return out


async def describe(bot, row) -> str:
    """One line summarising what a request would do if approved."""
    payload = json.loads(row["payload"] or "{}")
    kind = row["kind"]
    who = row["subject"] or "—"
    if row["member_id"]:
        member = await store.get_member(bot.db, row["member_id"])
        if member:
            who = member.gamertag

    if kind == "run":
        people = await run_members(bot, payload)
        bits = []
        if people:
            bits.append(f"**{len(people)}** {'person' if len(people) == 1 else 'people'}")
        if payload.get("keys"):
            bits.append(", ".join(payload["keys"]))
        if payload.get("score") and payload.get("trial"):
            bits.append(f"{payload['trial']} score {int(payload['score']):,} for "
                        f"{len(payload.get('score_members', []))}")
        if payload.get("unmapped"):
            bits.append("waiting on a role mapping")
        unclear = sum(1 for _, r in people if r not in reviews.ROLES_FOR_CLEARS)
        if unclear:
            bits.append(f"⚠️ {unclear} without a role")
        return " · ".join(bits) or "run"
    if kind == "achievements":
        keys = await store.expand_prerequisites(bot.db, payload.get("keys", []))
        role = ROLE_LABEL.get(row["role"], "⚠️ no role")
        return f"**{who}** · {role} · {', '.join(keys) or '—'}"
    if kind == "register":
        target, matches = await reviews.claim_target(bot.db, row["subject"])
        held, _ = await reviews.inherits(bot.db, row["subject"])
        if len(matches) > 1:
            warn = (f"  ⚠️ **matches {len(matches)} roster entries** — merge them with "
                    "`/member merge` first")
        elif target is not None:
            warn = (f"  ⚠️ **would inherit {held} achievement{'s' if held != 1 else ''}**"
                    if held else "  (existing record, nothing on it yet)")
        else:
            warn = "  (new name)"
        return f"<@{row['requester_id']}> claims **{who}**{warn}"
    if kind == "score":
        return f"**{who}** · {payload.get('trial', '?')} = {int(payload.get('score', 0)):,}"
    if kind == "parse":
        return f"**{who}** · {payload.get('label', '?')} = {int(payload.get('dps', 0)):,}"
    if kind == "report":
        text = payload.get("text", "")
        return (f"**{who}** · {payload.get('context', '')} · “{text[:80]}"
                + ("…”" if len(text) > 80 else "”"))
    if kind == "check":
        people = len(payload.get("recorded", []))
        bits = [f"<@{row['requester_id']}> recorded **{people}** "
                f"{'person' if people == 1 else 'people'} straight away"]
        if payload.get("keys"):
            bits.append(", ".join(payload["keys"]))
        if payload.get("score_raised"):
            bits.append(f"{payload['trial']} score {int(payload['score']):,} for "
                        f"{len(payload['score_raised'])}")
        return " · ".join(bits)
    return f"**{who}** · {kind}"


async def card(bot, row, image: str | None = None) -> discord.Embed:
    """The single-request card. `image` is the attachment://name of the copy of
    the evidence carried on the card message, when there is one."""
    payload = json.loads(row["payload"] or "{}")
    e = embeds.base(
        bot.brand,
        f"{reviews.KIND_EMOJI.get(row['kind'], '📝')}  "
        f"{reviews.KIND_LABEL.get(row['kind'], row['kind'])} — waiting for an officer",
        await describe(bot, row),
    )

    if row["kind"] == "report":
        e.add_field(name="What they said", value=payload.get("text") or "—", inline=False)
        e.add_field(name="What the buttons do",
                    value="**Approve** once you've dealt with it, **Reject** if nothing's "
                          "wrong. Neither changes the record: fix it with `/achievement "
                          "take`, or find the change in `/audit member:` and `/undo` it.",
                    inline=False)

    if row["kind"] == "check":
        lines = []
        for member_id, role, added in payload.get("recorded", []):
            member = await store.get_member(bot.db, member_id)
            if member is not None and added:
                lines.append(f"{ROLE_EMOJI.get(role, '')} **{member.gamertag}** +{', '.join(added)}")
        if lines:
            e.add_field(name="What it recorded", value=embeds.lines_within(lines), inline=False)
        e.add_field(name="What the buttons do",
                    value="**Approve** if it looks right. **Reject** undoes everything "
                          "this post recorded.", inline=False)
        if payload.get("evidence"):
            e.add_field(name="Evidence", value=f"[the bot's copy]({payload['evidence']})",
                        inline=True)

    if row["kind"] == "run":
        people = await run_members(bot, payload)
        for role in reviews.ROLES_FOR_CLEARS:
            names = [m.gamertag for m, r in people if r == role]
            if names:
                e.add_field(name=f"{ROLE_EMOJI[role]} {ROLE_LABEL[role]}",
                            value=embeds.lines_within([", ".join(names)]), inline=False)
        unclear = [m.gamertag for m, r in people if r not in reviews.ROLES_FOR_CLEARS]
        if unclear:
            e.add_field(name="❓ No clear role — pick one below",
                        value=embeds.lines_within([", ".join(unclear)]), inline=False)

    e.add_field(name="Raised by", value=f"<@{row['requester_id']}>", inline=True)
    if row["note"]:
        e.add_field(name="Why it's here", value=row["note"], inline=True)
    if row["source_url"]:
        e.add_field(name="Post", value=f"[jump]({row['source_url']})", inline=True)
    if image:
        e.set_image(url=image)
    elif row["kind"] in ("register", "score", "parse") or payload.get("score"):
        e.add_field(name="Evidence", value="⚠️ no screenshot was attached", inline=False)
    e.set_footer(text=f"{bot.brand.name} • request #{row['id']}")
    return e


async def post_card(bot, row, *, proof: discord.Attachment | None = None,
                    reply_to: discord.Message | None = None,
                    channel: discord.abc.Messageable | None = None) -> discord.Message | None:
    """Post a request's card, copying the evidence onto it, and remember where it went."""
    file = await copy_of(proof) if proof is not None else None
    embed = await card(bot, row, f"attachment://{file.filename}" if file else None)
    view = ReviewCard(bot, with_role=reviews.needs_role(row))
    kwargs = {"embed": embed, "view": view}
    if file is not None:
        kwargs["file"] = file
    try:
        if reply_to is not None:
            posted = await reply_to.reply(mention_author=False, **kwargs)
        elif channel is not None:
            posted = await channel.send(**kwargs)
        else:
            return None
    except discord.HTTPException:
        log.warning("could not post the card for request #%s", row["id"])
        return None
    await reviews.attach_card(bot.db, row["id"], posted.id, posted.channel.id)
    return posted


def image_on(message: discord.Message | None) -> str | None:
    """The card's own copy of its evidence, so a re-render keeps showing it."""
    if message is not None and message.attachments:
        return f"attachment://{message.attachments[0].filename}"
    return None


def card_link(row, guild) -> str:
    """Where the request's card is, or failing that the post it came from."""
    if guild is not None and row["channel_id"] and row["message_id"]:
        return f"https://discord.com/channels/{guild.id}/{row['channel_id']}/{row['message_id']}"
    return row["source_url"] or ""


async def approve_one(bot, row, actor: discord.abc.User, guild) -> reviews.Applied:
    """The one path from request to guild data. Every change it makes links back to
    the card the officer approved, which carries the screenshot."""
    result = await reviews.apply(bot.db, row, actor.id, str(actor), card_link(row, guild))
    if not result.ok:
        return result
    await reviews.close(bot.db, row["id"], "approved", actor.id)
    await bot.audit(actor, f"{row['kind']} approved", f"#{row['id']} — {result.summary}")

    cog = bot.get_cog("Submissions")
    if row["kind"] == "run" and result.run is not None:
        payload = json.loads(row["payload"] or "{}")
        for member, role, added in result.run.recorded:
            if added:
                await bot.sync_roles(guild, member, "run approved")
                if cog:
                    await cog.notify(member, role, added)
        await bot.activity("run approved", f"#{row['id']} — {result.summary}")
        if payload.get("score") and cog:
            for member in result.run.score_raised:
                await cog.notify_score(member, payload["trial"], int(payload["score"]))
    elif result.member and row["kind"] == "achievements":
        await bot.sync_roles(guild, result.member, "achievements approved")
        if cog:
            await cog.notify(result.member, row["role"],
                             json.loads(row["payload"] or "{}").get("keys", []))
    return result


async def reject_one(bot, row, actor: discord.abc.User, guild) -> str:
    """The one path for turning a request down. Rejecting a spot check undoes
    everything that post recorded; rejecting anything else just discards it."""
    summary = ""
    if row["kind"] == "check":
        payload = json.loads(row["payload"] or "{}")
        undone = 0
        for entry in await bot.db.all(
                "SELECT id FROM audit_log WHERE evidence = ? AND evidence <> '' AND undone = 0 "
                "ORDER BY id DESC", (payload.get("evidence", ""),)):
            if await store.undo_action(bot.db, entry["id"]) is not None:
                undone += 1
        for member_id, _, _ in payload.get("recorded", []):
            member = await store.get_member(bot.db, member_id)
            if member is not None:
                await bot.sync_roles(guild, member, "spot check undone")
        summary = f"undid {undone} change{'s' if undone != 1 else ''}"
    await reviews.close(bot.db, row["id"], "rejected", actor.id)
    await bot.audit(actor, f"{row['kind']} rejected",
                    f"#{row['id']}" + (f" — {summary}" if summary else ""))
    return summary


class CardRole(discord.ui.Select):
    """Static options, so it survives a restart like the buttons do."""

    def __init__(self):
        super().__init__(
            placeholder="Set the role for anyone without one…", custom_id=ROLE_ID, row=1,
            options=[discord.SelectOption(label=ROLE_LABEL[r], value=r, emoji=ROLE_EMOJI[r])
                     for r in reviews.ROLES_FOR_CLEARS])

    async def callback(self, interaction: discord.Interaction):
        view: ReviewCard = self.view
        row = await view.row_for(interaction)
        if row is None:
            return
        await reviews.set_role(view.bot.db, row["id"], self.values[0])
        row = await reviews.get(view.bot.db, row["id"])
        await interaction.response.edit_message(
            embed=await card(view.bot, row, image_on(interaction.message)),
            view=ReviewCard(view.bot, with_role=reviews.needs_role(row)))


class ReviewCard(discord.ui.View):
    """Persistent: the buttons keep working after a restart."""

    def __init__(self, bot, with_role: bool = True):
        super().__init__(timeout=None)
        self.bot = bot
        if with_role:
            self.add_item(CardRole())

    async def row_for(self, interaction: discord.Interaction):
        if not await user_is_admin(interaction):
            await interaction.response.send_message(
                embed=embeds.error("Only officers can decide these."), ephemeral=True)
            return None
        row = await reviews.by_message(self.bot.db, interaction.message.id)
        if row is None:
            await interaction.response.send_message(
                embed=embeds.warn("Already dealt with."), ephemeral=True)
        return row

    @discord.ui.button(label="Approve", emoji="✅", style=discord.ButtonStyle.success,
                       custom_id=APPROVE_ID, row=0)
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button):
        row = await self.row_for(interaction)
        if row is None:
            return
        result = await approve_one(self.bot, row, interaction.user, interaction.guild)
        if not result.ok:
            return await interaction.response.send_message(
                embed=embeds.error(result.summary), ephemeral=True)
        await interaction.response.edit_message(
            embed=embeds.success(f"Recorded by {interaction.user.mention} — {result.summary}"),
            view=None)

    @discord.ui.button(label="Reject", emoji="🗑️", style=discord.ButtonStyle.secondary,
                       custom_id=REJECT_ID, row=0)
    async def reject(self, interaction: discord.Interaction, _: discord.ui.Button):
        row = await self.row_for(interaction)
        if row is None:
            return
        summary = await reject_one(self.bot, row, interaction.user, interaction.guild)
        await interaction.response.edit_message(
            embed=embeds.warn(f"Rejected by {interaction.user.mention}."
                              + (f" It {summary}." if summary else "")), view=None)


class QueueView(views.OwnedView):
    """A page of pending requests with batch actions."""

    def __init__(self, bot, owner_id: int, kind: str = "", guild_id: int = 0):
        super().__init__(owner_id, timeout=600)
        self.bot = bot
        self.kind = kind
        self.guild_id = guild_id
        self.page = 0
        self.rows: list = []
        self.picked: list[int] = []

    def card_link(self, row) -> str:
        if self.guild_id and row["channel_id"] and row["message_id"]:
            return (f" [card](https://discord.com/channels/{self.guild_id}/"
                    f"{row['channel_id']}/{row['message_id']})")
        return ""

    async def build(self) -> discord.Embed:
        total = await reviews.pending_count(self.bot.db, self.kind)
        pages = max(1, -(-total // PER_PAGE))
        self.page = min(self.page, pages - 1)
        self.rows = await reviews.pending(
            self.bot.db, self.kind, PER_PAGE, self.page * PER_PAGE)

        self.clear_items()
        if self.rows:
            self.add_item(PickSelect(await self._options()))
            self.add_item(ApproveSelected())
            self.add_item(RejectSelected())
            self.add_item(ApproveAll())
        self.add_item(Pager("◀", -1, self.page == 0))
        self.add_item(Pager("▶", 1, self.page >= pages - 1))
        self.add_item(Refresh())
        if any(reviews.needs_role(r) for r in self.rows):
            self.add_item(RoleForSelected())

        counts = await reviews.counts_by_kind(self.bot.db)
        breakdown = "  ·  ".join(
            f"{reviews.KIND_EMOJI[k]} {counts[k]} {reviews.KIND_LABEL[k].lower()}"
            for k in reviews.KINDS if counts.get(k)) or "nothing waiting"

        e = embeds.base(self.bot.brand, f"📥  Review queue — {total} waiting", breakdown)
        if not self.rows:
            e.add_field(name="​", value="✅ Nothing needs you right now.", inline=False)
        else:
            lines = []
            for row in self.rows:
                proof = " 📎" if row["proof_url"] else ""
                lines.append(
                    f"`#{row['id']}` {reviews.KIND_EMOJI.get(row['kind'], '📝')} "
                    f"{await describe(self.bot, row)}{proof}{self.card_link(row)}"
                    + (f"\n　　_{row['note']}_" if row["note"] else ""))
            e.add_field(name="​", value=embeds.lines_within(lines), inline=False)
        e.set_footer(text=f"{self.bot.brand.name} • page {self.page + 1}/{pages}"
                          " • 📎 has a screenshot")
        return e

    async def _options(self) -> list[discord.SelectOption]:
        out = []
        for row in self.rows:
            text = await describe(self.bot, row)
            for markup in ("**", "_", "`"):
                text = text.replace(markup, "")
            out.append(discord.SelectOption(
                label=f"#{row['id']} {reviews.KIND_LABEL.get(row['kind'], row['kind'])}"[:100],
                value=str(row["id"]), description=text[:100]))
        return out

    async def refresh(self, interaction: discord.Interaction, extra: str = ""):
        embed = await self.build()
        if extra:
            embed.description = f"{extra}\n\n{embed.description}"
        await interaction.response.edit_message(embed=embed, view=self)

    async def decide(self, interaction: discord.Interaction, ids: list[int], approve: bool):
        done, failed = 0, []
        for request_id in ids:
            row = await reviews.get(self.bot.db, request_id)
            if row is None or row["status"] != "pending":
                continue
            if approve:
                result = await approve_one(self.bot, row, interaction.user, interaction.guild)
                if result.ok:
                    done += 1
                else:
                    failed.append(f"#{request_id} {result.summary}")
            else:
                await reject_one(self.bot, row, interaction.user, interaction.guild)
                done += 1
        self.picked = []
        word = "approved" if approve else "rejected"
        note = f"✅ {done} {word}." + ("\n⚠️ " + "; ".join(failed[:3]) if failed else "")
        await self.refresh(interaction, note)


class PickSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(placeholder="Pick the ones to act on…", row=0,
                         min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        self.view.picked = [int(v) for v in self.values]
        await self.view.refresh(interaction, f"Selected {len(self.values)}.")


class RoleForSelected(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Set the role on the selected ones…", row=3,
            options=[discord.SelectOption(label=ROLE_LABEL[r], value=r, emoji=ROLE_EMOJI[r])
                     for r in reviews.ROLES_FOR_CLEARS])

    async def callback(self, interaction: discord.Interaction):
        if not self.view.picked:
            return await interaction.response.send_message(
                embed=embeds.warn("Pick some from the first menu first."), ephemeral=True)
        changed = 0
        for request_id in self.view.picked:
            changed += await reviews.set_role(self.view.bot.db, request_id, self.values[0])
        await self.view.refresh(interaction,
                                f"Set {ROLE_LABEL[self.values[0]]} on {changed}.")


class ApproveSelected(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Approve selected", style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not self.view.picked:
            return await interaction.response.send_message(
                embed=embeds.warn("Pick some from the menu first."), ephemeral=True)
        await self.view.decide(interaction, self.view.picked, True)


class RejectSelected(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Reject selected", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not self.view.picked:
            return await interaction.response.send_message(
                embed=embeds.warn("Pick some from the menu first."), ephemeral=True)
        await self.view.decide(interaction, self.view.picked, False)


class ApproveAll(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Approve this page", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        await self.view.decide(interaction, [r["id"] for r in self.view.rows], True)


class Pager(discord.ui.Button):
    def __init__(self, label: str, step: int, disabled: bool):
        super().__init__(label=label, style=discord.ButtonStyle.secondary,
                         row=2, disabled=disabled)
        self.step = step

    async def callback(self, interaction: discord.Interaction):
        self.view.page += self.step
        self.view.picked = []
        await self.view.refresh(interaction)


class Refresh(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Refresh", emoji="🔄",
                         style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.view.picked = []
        await self.view.refresh(interaction)


class ReviewQueue(commands.Cog):
    """Review queue"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ReviewCard(self.bot))

    @app_commands.command(description="Everything waiting on an officer")
    @app_commands.describe(kind="Only show one sort of request")
    @app_commands.choices(kind=[
        app_commands.Choice(name=reviews.KIND_LABEL[k], value=k) for k in reviews.KINDS])
    @admin_only()
    async def queue(self, interaction: discord.Interaction,
                    kind: app_commands.Choice[str] | None = None):
        view = QueueView(self.bot, interaction.user.id, kind.value if kind else "",
                         interaction.guild.id if interaction.guild else 0)
        await interaction.response.send_message(embed=await view.build(), view=view,
                                                ephemeral=True)


async def setup(bot):
    await bot.add_cog(ReviewQueue(bot))
