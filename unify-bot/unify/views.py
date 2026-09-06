"""Reusable interactive components: pager, confirm dialog, and the profile
browser (role menu + trial drill-down)."""
from __future__ import annotations

from typing import Awaitable, Callable

import discord

from . import embeds, store
from .config import ROLE_EMOJI, ROLE_LABEL, ROLES


class OwnedView(discord.ui.View):
    """A view only the person who ran the command can drive."""

    def __init__(self, owner_id: int, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "That menu belongs to someone else — run the command yourself to get your own.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Paginator(OwnedView):
    def __init__(self, owner_id: int, pages: list[discord.Embed]):
        super().__init__(owner_id)
        self.pages = pages
        self.index = 0
        if len(pages) <= 1:
            self.clear_items()
        self._sync()

    def _sync(self) -> None:
        if not self.children:
            return
        self.first.disabled = self.prev.disabled = self.index == 0
        self.next.disabled = self.last.disabled = self.index >= len(self.pages) - 1
        self.counter.label = f"{self.index + 1} / {len(self.pages)}"

    async def _go(self, interaction: discord.Interaction, index: int) -> None:
        self.index = max(0, min(index, len(self.pages) - 1))
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def first(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._go(interaction, 0)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.primary)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._go(interaction, self.index - 1)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, _: discord.ui.Button):
        pass

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._go(interaction, self.index + 1)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._go(interaction, len(self.pages) - 1)


class Confirm(OwnedView):
    """Await `view.result` after `view.wait()`. None means it timed out."""

    def __init__(self, owner_id: int, confirm_label: str = "Confirm",
                 style: discord.ButtonStyle = discord.ButtonStyle.danger):
        super().__init__(owner_id, timeout=120)
        self.result: bool | None = None
        self.confirm.label = confirm_label
        self.confirm.style = style

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.result = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.result = False
        await interaction.response.defer()
        self.stop()


class RoleSelect(discord.ui.Select):
    def __init__(self, current: str, roles: tuple[str, ...] = ROLES + ("account",)):
        super().__init__(
            placeholder="Change role…",
            options=[
                discord.SelectOption(label=ROLE_LABEL[r], value=r, emoji=ROLE_EMOJI[r],
                                     default=r == current)
                for r in roles
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: ProfileView = self.view
        view.role = self.values[0]
        view.trial_key = None
        await view.refresh(interaction)


class TrialSelect(discord.ui.Select):
    def __init__(self, trials: list[dict], current: str | None):
        super().__init__(
            placeholder="Open a trial for the full breakdown…",
            options=[
                discord.SelectOption(
                    label=t["name"][:100], value=t["key"],
                    emoji=embeds.as_emoji(t["emoji"]),
                    description=f"{t['done']}/{t['total']} done",
                    default=t["key"] == current,
                )
                for t in trials[:25]
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: ProfileView = self.view
        view.trial_key = self.values[0]
        await view.refresh(interaction)


class ProfileView(OwnedView):
    """Role menu + trial drill-down over one member's record."""

    def __init__(self, bot, owner_id: int, member: store.Member, role: str = "dps",
                 avatar_url: str | None = None):
        super().__init__(owner_id)
        self.bot = bot
        self.member = member
        self.role = role
        self.trial_key: str | None = None
        self.avatar_url = avatar_url

    async def build(self) -> discord.Embed:
        db = self.bot.db
        rows = await store.progress(db, self.member.id, self.role)
        self.clear_items()
        self.add_item(RoleSelect(self.role))
        self.add_item(TrialSelect(rows, self.trial_key))

        if self.trial_key:
            trial = dict(await db.one("SELECT * FROM trials WHERE key = ?", (self.trial_key,)))
            detail = await store.trial_detail(db, self.member.id, self.role, self.trial_key)
            self.add_item(BackButton())
            return embeds.trial_embed(self.bot.brand, self.member.gamertag, self.role, trial, detail)

        return embeds.profile_embed(
            self.bot.brand,
            self.member.gamertag,
            self.member.discord_id,
            self.role,
            rows,
            await store.titles(db, self.member.id),
            await db.val("SELECT COALESCE(SUM(score),0) FROM scores WHERE member_id=?",
                         (self.member.id,), 0),
            await store.points(db, self.member.id),
            self.avatar_url,
        )

    async def refresh(self, interaction: discord.Interaction) -> None:
        embed = await self.build()
        await interaction.response.edit_message(embed=embed, view=self)


class BackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back to overview", emoji="↩️",
                         style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        view: ProfileView = self.view
        view.trial_key = None
        await view.refresh(interaction)


class ActionButton(discord.ui.Button):
    """Button wired to a plain coroutine - keeps ad-hoc wizards short."""

    def __init__(self, label: str, handler: Callable[[discord.Interaction], Awaitable[None]],
                 style: discord.ButtonStyle = discord.ButtonStyle.primary, **kwargs):
        super().__init__(label=label, style=style, **kwargs)
        self.handler = handler

    async def callback(self, interaction: discord.Interaction):
        await self.handler(interaction)
