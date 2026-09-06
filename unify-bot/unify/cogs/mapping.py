"""Teaching the bot which Discord role means which achievement.

This is the only setup step that really matters, so it has three ways in:
auto-detect for the whole server, a two-tap picker, and a plain slash command."""
from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, views
from ..checks import admin_only
from ..config import ROLES, ROLE_EMOJI, ROLE_LABEL


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class MapRoleView(views.OwnedView):
    """Two taps: pick the trial, then pick the achievement."""

    def __init__(self, bot, owner_id: int, role: discord.Role):
        super().__init__(owner_id)
        self.bot = bot
        self.role = role
        self.trial_key: str | None = None

    async def build(self) -> discord.Embed:
        self.clear_items()
        trials = await self.bot.db.all("SELECT * FROM trials ORDER BY sort")
        self.add_item(_TrialPicker(trials, self.trial_key))
        if self.trial_key:
            rows = await self.bot.db.all(
                "SELECT * FROM achievements WHERE trial_key = ? ORDER BY sort", (self.trial_key,)
            )
            self.add_item(_AchievementPicker(rows))
        self.add_item(_RoleKindPicker())

        e = embeds.base(
            self.bot.brand,
            f"🔗  Map the role {self.role.name}",
            "Pick the trial first, then the achievement it stands for.\n"
            "If this role means **Tank / Healer / DPS** instead, use the bottom menu.",
        )
        e.add_field(name="Discord role", value=self.role.mention, inline=False)
        return e

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self.build(), view=self)

    async def save(self, interaction: discord.Interaction, kind: str, value: str, label: str):
        await self.bot.db.run(
            "INSERT INTO role_map(discord_role_id, kind, value, label) VALUES(?,?,?,?) "
            "ON CONFLICT(discord_role_id) DO UPDATE SET kind=excluded.kind, "
            "value=excluded.value, label=excluded.label",
            (self.role.id, kind, value, self.role.name),
        )
        await self.bot.audit(interaction.user, "role mapped", f"{self.role.name} -> {label}")
        self.clear_items()
        await interaction.response.edit_message(
            embed=embeds.success(f"**{self.role.name}** now means **{label}**."), view=None
        )
        self.stop()


class _TrialPicker(discord.ui.Select):
    def __init__(self, trials, current):
        super().__init__(
            placeholder="1) Which trial?",
            row=0,
            options=[
                discord.SelectOption(label=t["name"][:100], value=t["key"],
                                     emoji=embeds.as_emoji(t["emoji"]),
                                     default=t["key"] == current)
                for t in trials[:25]
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.trial_key = self.values[0]
        await self.view.refresh(interaction)


class _AchievementPicker(discord.ui.Select):
    def __init__(self, rows):
        super().__init__(
            placeholder="2) Which achievement?",
            row=1,
            options=[
                discord.SelectOption(label=r["name"][:100], value=r["key"],
                                     description=r["key"])
                for r in rows[:25]
            ],
        )
        self.names = {r["key"]: r["name"] for r in rows}

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        await self.view.save(interaction, "achievement", key, self.names[key])


class _RoleKindPicker(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="...or: this role means a raid role",
            row=2,
            options=[discord.SelectOption(label=ROLE_LABEL[r], value=r, emoji=ROLE_EMOJI[r])
                     for r in ROLES],
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        await self.view.save(interaction, "role", value, ROLE_LABEL[value])


class Mapping(commands.Cog):
    """Role mapping"""

    def __init__(self, bot):
        self.bot = bot

    group = app_commands.Group(name="map", description="Link Discord roles to achievements")

    # ------------------------------------------------------------------ auto
    async def propose(self, guild: discord.Guild) -> list[tuple[discord.Role, str, str, str]]:
        """Guess a mapping for every unmapped role. (role, kind, value, label)"""
        db = self.bot.db
        taken = {r[0] for r in await db.all("SELECT discord_role_id FROM role_map")}
        achievements = await db.all(
            "SELECT a.key, a.name, t.short FROM achievements a JOIN trials t ON t.key=a.trial_key"
        )
        by_key = {normalize(a["key"]): a for a in achievements}
        by_name = {normalize(a["name"]): a for a in achievements}
        by_short_name = {normalize(a["short"] + a["name"]): a for a in achievements}

        out = []
        for role in guild.roles:
            if role.is_default() or role.managed or role.id in taken:
                continue
            n = normalize(role.name)
            if n in ROLES:
                out.append((role, "role", n, ROLE_LABEL[n]))
                continue
            hit = by_key.get(n) or by_name.get(n) or by_short_name.get(n)
            if hit:
                out.append((role, "achievement", hit["key"], hit["name"]))
        return out

    @group.command(name="auto", description="Scan the server and match roles to achievements for you")
    @admin_only()
    async def auto(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        found = await self.propose(interaction.guild)
        if not found:
            return await interaction.followup.send(
                embed=embeds.warn(
                    "I couldn't match any new roles by name.\n"
                    "Use `/map role` (two taps, no typing) for the ones that are left - "
                    "`/map missing` shows which achievements still need a role."
                )
            )
        preview = "\n".join(f"{r.mention} -> **{label}**" for r, _, _, label in found[:25])
        extra = f"\n...and {len(found) - 25} more" if len(found) > 25 else ""
        e = embeds.base(
            self.bot.brand,
            f"🔎  Found {len(found)} matches",
            "Have a read. Press **Save all** if it looks right - you can change any single "
            "one later with `/map role`.\n\n" + preview + extra,
        )
        view = views.Confirm(interaction.user.id, "Save all", discord.ButtonStyle.success)
        message = await interaction.followup.send(embed=e, view=view, wait=True)
        view.message = message
        await view.wait()
        if not view.result:
            return await message.edit(embed=embeds.warn("Cancelled - nothing was changed."),
                                      view=None)
        await self.bot.db.run_many([
            ("INSERT INTO role_map(discord_role_id, kind, value, label) VALUES(?,?,?,?) "
             "ON CONFLICT(discord_role_id) DO UPDATE SET kind=excluded.kind, "
             "value=excluded.value, label=excluded.label",
             (role.id, kind, value, role.name))
            for role, kind, value, _ in found
        ])
        await self.bot.audit(interaction.user, "role mapping", f"auto-mapped {len(found)} roles")
        await message.edit(embed=embeds.success(f"Saved {len(found)} role mappings."), view=None)

    # ------------------------------------------------------------------ manual
    @group.command(name="role", description="Map one Discord role, guided (no typing)")
    @admin_only()
    async def role_cmd(self, interaction: discord.Interaction, role: discord.Role):
        view = MapRoleView(self.bot, interaction.user.id, role)
        await interaction.response.send_message(embed=await view.build(), view=view,
                                                ephemeral=True)

    @group.command(name="set", description="Map a role to an achievement in one go")
    @app_commands.autocomplete(achievement=autocomplete.achievements)
    @admin_only()
    async def set_cmd(self, interaction: discord.Interaction, role: discord.Role, achievement: str):
        row = await self.bot.db.one("SELECT * FROM achievements WHERE key = ?", (achievement,))
        if row is None:
            return await interaction.response.send_message(
                embed=embeds.error(f"`{achievement}` isn't an achievement I know."), ephemeral=True)
        await self.bot.db.run(
            "INSERT INTO role_map(discord_role_id, kind, value, label) VALUES(?,'achievement',?,?) "
            "ON CONFLICT(discord_role_id) DO UPDATE SET kind='achievement', "
            "value=excluded.value, label=excluded.label",
            (role.id, achievement, role.name),
        )
        await self.bot.audit(interaction.user, "role mapped", f"{role.name} -> {row['name']}")
        await interaction.response.send_message(
            embed=embeds.success(f"{role.mention} now means **{row['name']}**."))

    @group.command(name="remove", description="Forget the mapping for a role")
    @admin_only()
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        cur = await self.bot.db.run("DELETE FROM role_map WHERE discord_role_id = ?", (role.id,))
        if cur.rowcount:
            await self.bot.audit(interaction.user, "role unmapped", role.name)
            await interaction.response.send_message(
                embed=embeds.success(f"{role.mention} is no longer mapped."))
        else:
            await interaction.response.send_message(
                embed=embeds.warn("That role wasn't mapped."), ephemeral=True)

    # ------------------------------------------------------------------ inspect
    @group.command(name="list", description="Show every role mapping")
    @admin_only()
    async def list_cmd(self, interaction: discord.Interaction):
        rows = await self.bot.db.all(
            "SELECT rm.*, a.name AS aname FROM role_map rm "
            "LEFT JOIN achievements a ON a.key = rm.value ORDER BY rm.kind, rm.label"
        )
        if not rows:
            return await interaction.response.send_message(
                embed=embeds.warn("Nothing mapped yet - start with `/map auto`."), ephemeral=True)
        lines = [
            f"<@&{r['discord_role_id']}> -> **"
            f"{ROLE_LABEL.get(r['value'], r['aname'] or r['value'])}**"
            for r in rows
        ]
        pages = [
            embeds.base(self.bot.brand, f"🔗  Role mappings ({len(rows)})",
                        "\n".join(lines[start:start + 20]))
            for start in range(0, len(lines), 20)
        ]
        view = views.Paginator(interaction.user.id, pages)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

    @group.command(name="missing", description="Which achievements still have no Discord role")
    @admin_only()
    async def missing(self, interaction: discord.Interaction):
        rows = await self.bot.db.all(
            "SELECT a.key, a.name, t.short FROM achievements a "
            "JOIN trials t ON t.key = a.trial_key "
            "WHERE a.key NOT IN (SELECT value FROM role_map WHERE kind='achievement') "
            "ORDER BY t.sort, a.sort"
        )
        mapped_roles = await self.bot.db.all("SELECT value FROM role_map WHERE kind='role'")
        note = ""
        if len(mapped_roles) < len(ROLES):
            have = {r[0] for r in mapped_roles}
            note = ("\n\n⚠️ No Discord role mapped for: "
                    + ", ".join(ROLE_LABEL[r] for r in ROLES if r not in have)
                    + " - people can still type `tank` / `healer` / `dps` in the post.")
        if not rows:
            return await interaction.response.send_message(
                embed=embeds.success("Every achievement has a Discord role." + note),
                ephemeral=True)
        e = embeds.base(
            self.bot.brand, f"🕳️  {len(rows)} achievements without a role",
            "\n".join(f"`{r['short']}` {r['name']}  (`{r['key']}`)" for r in rows[:40])
            + (f"\n...and {len(rows) - 40} more" if len(rows) > 40 else "") + note,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Mapping(bot))
