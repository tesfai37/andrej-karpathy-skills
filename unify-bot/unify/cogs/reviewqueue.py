"""Everything waiting on an officer, in one place.

Two ways in: the card the bot posts under a submission, and /queue for clearing
a backlog in batches. Both funnel through the same approve path, so there is
exactly one piece of code that turns a request into guild data."""
from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from .. import embeds, reviews, store, views
from ..checks import admin_only, user_is_admin
from ..config import ROLE_LABEL

APPROVE_ID = "unify:rev:approve"
REJECT_ID = "unify:rev:reject"
PER_PAGE = 8


async def describe(bot, row) -> str:
    """One line summarising what a request would do if approved."""
    payload = json.loads(row["payload"] or "{}")
    kind = row["kind"]
    who = row["subject"] or "—"
    if row["member_id"]:
        member = await store.get_member(bot.db, row["member_id"])
        if member:
            who = member.gamertag

    if kind == "achievements":
        keys = await store.expand_prerequisites(bot.db, payload.get("keys", []))
        role = ROLE_LABEL.get(row["role"], row["role"] or "?")
        return f"**{who}** · {role} · {', '.join(keys) or '—'}"
    if kind == "register":
        held, scores = await reviews.inherits(bot.db, row["subject"])
        warn = (f"  ⚠️ **would inherit {held} achievements**" if held else "  (new name)")
        return f"<@{row['requester_id']}> claims **{who}**{warn}"
    if kind == "score":
        return f"**{who}** · {payload.get('trial', '?')} = {int(payload.get('score', 0)):,}"
    if kind == "parse":
        return f"**{who}** · {payload.get('label', '?')} = {int(payload.get('dps', 0)):,}"
    return f"**{who}** · {kind}"


async def card(bot, row) -> discord.Embed:
    """The single-request card posted under a submission."""
    e = embeds.base(
        bot.brand,
        f"{reviews.KIND_EMOJI.get(row['kind'], '📝')}  "
        f"{reviews.KIND_LABEL.get(row['kind'], row['kind'])} — waiting for an officer",
        await describe(bot, row),
    )
    e.add_field(name="Raised by", value=f"<@{row['requester_id']}>", inline=True)
    if row["note"]:
        e.add_field(name="Why it's here", value=row["note"], inline=True)
    if row["source_url"]:
        e.add_field(name="Post", value=f"[jump]({row['source_url']})", inline=True)
    if row["proof_url"]:
        e.set_image(url=row["proof_url"])
    e.set_footer(text=f"{bot.brand.name} • request #{row['id']}")
    return e


async def approve_one(bot, row, actor: discord.abc.User, guild) -> reviews.Applied:
    """The one path from request to guild data."""
    result = await reviews.apply(bot.db, row, actor.id, str(actor))
    if not result.ok:
        return result
    await reviews.close(bot.db, row["id"], "approved", actor.id)
    await bot.audit(actor, f"{row['kind']} approved",
                    f"#{row['id']} — {result.summary}")
    if result.member and row["kind"] == "achievements":
        await bot.sync_roles(guild, result.member, "achievements approved")
        cog = bot.get_cog("Submissions")
        if cog:
            await cog.notify(result.member, row["role"],
                             json.loads(row["payload"] or "{}").get("keys", []))
    return result


class ReviewCard(discord.ui.View):
    """Persistent: the buttons keep working after a restart."""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _row(self, interaction: discord.Interaction):
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
                       custom_id=APPROVE_ID)
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button):
        row = await self._row(interaction)
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
                       custom_id=REJECT_ID)
    async def reject(self, interaction: discord.Interaction, _: discord.ui.Button):
        row = await self._row(interaction)
        if row is None:
            return
        await reviews.close(self.bot.db, row["id"], "rejected", interaction.user.id)
        await self.bot.audit(interaction.user, f"{row['kind']} rejected", f"#{row['id']}")
        await interaction.response.edit_message(
            embed=embeds.warn(f"Rejected by {interaction.user.mention}."), view=None)


class QueueView(views.OwnedView):
    """A page of pending requests with batch actions."""

    def __init__(self, bot, owner_id: int, kind: str = ""):
        super().__init__(owner_id, timeout=600)
        self.bot = bot
        self.kind = kind
        self.page = 0
        self.rows: list = []

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
                lines.append(
                    f"`#{row['id']}` {reviews.KIND_EMOJI.get(row['kind'], '📝')} "
                    f"{await describe(self.bot, row)}"
                    + (f"\n　　_{row['note']}_" if row["note"] else ""))
            e.add_field(name="​", value=embeds.lines_within(lines), inline=False)
        e.set_footer(text=f"{self.bot.brand.name} • page {self.page + 1}/{pages}")
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
                await reviews.close(self.bot.db, request_id, "rejected", interaction.user.id)
                await self.bot.audit(interaction.user, f"{row['kind']} rejected",
                                     f"#{request_id}")
                done += 1
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


class ApproveSelected(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Approve selected", style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction: discord.Interaction):
        picked = getattr(self.view, "picked", [])
        if not picked:
            return await interaction.response.send_message(
                embed=embeds.warn("Pick some from the menu first."), ephemeral=True)
        await self.view.decide(interaction, picked, True)


class RejectSelected(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Reject selected", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        picked = getattr(self.view, "picked", [])
        if not picked:
            return await interaction.response.send_message(
                embed=embeds.warn("Pick some from the menu first."), ephemeral=True)
        await self.view.decide(interaction, picked, False)


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
        view = QueueView(self.bot, interaction.user.id, kind.value if kind else "")
        await interaction.response.send_message(embed=await view.build(), view=view,
                                                ephemeral=True)


async def setup(bot):
    await bot.add_cog(ReviewQueue(bot))
