"""Handing out the Discord roles people have earned.

Off until an admin turns it on (`/config set key:role_sync value:true`), because
the first run can hand out a lot of roles at once."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, rolesync, store, views
from ..checks import admin_only

REASON = "Unify achievement sync"


def describe(plan: rolesync.SyncPlan) -> str:
    bits = []
    if plan.add:
        bits.append("➕ " + ", ".join(r.mention for r in plan.add))
    if plan.remove:
        bits.append("➖ " + ", ".join(r.mention for r in plan.remove))
    if plan.blocked:
        bits.append("🔒 can't manage: " + ", ".join(r.mention for r in plan.blocked))
    return "\n".join(bits) or "_already up to date_"


class Sync(commands.Cog):
    """Discord role sync"""

    def __init__(self, bot):
        self.bot = bot

    group = app_commands.Group(name="sync",
                               description="Match Discord roles to earned achievements")

    # ------------------------------------------------------------------ check
    @group.command(name="check", description="Can I actually hand out your achievement roles?")
    @admin_only()
    async def check(self, interaction: discord.Interaction):
        guild = interaction.guild
        mapping = await rolesync.achievement_roles(self.bot.db)
        me = guild.me
        if me is None:
            return await interaction.response.send_message(
                embed=embeds.error("I can't see myself in this server yet — try again in a moment."),
                ephemeral=True)
        has_permission = me.guild_permissions.manage_roles

        blocked, missing, fine = [], [], 0
        for role_id in set(mapping.values()):
            role = guild.get_role(role_id)
            if role is None:
                missing.append(role_id)
            elif rolesync.can_manage(guild, role):
                fine += 1
            else:
                blocked.append(role)

        on = await self.bot.setting("role_sync")
        e = embeds.base(
            self.bot.brand, "🔍  Role sync check",
            f"Sync is currently **{'on' if on else 'off'}**"
            + ("" if on else " — turn it on with `/config set key:role_sync value:true`"),
        )
        e.add_field(name="Manage Roles permission",
                    value="✅ yes" if has_permission else
                          "❌ no — add it in Server Settings → Roles", inline=False)
        e.add_field(name="Roles I can hand out", value=f"**{fine}** of {len(set(mapping.values()))}",
                    inline=False)
        if blocked:
            e.add_field(
                name="🔒 Out of reach",
                value=embeds.lines_within([r.mention for r in blocked])
                      + f"\n\nDrag **{me.top_role.name}** above these in "
                        "Server Settings → Roles and they'll work.",
                inline=False)
        if missing:
            e.add_field(name="🗑️ Mapped but deleted",
                        value=f"{len(missing)} role(s) no longer exist. "
                              "`/map list` shows them; `/map remove` clears them.",
                        inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ------------------------------------------------------------------ one member
    @group.command(name="member", description="Fix one person's roles now")
    @app_commands.autocomplete(member=autocomplete.members)
    @admin_only()
    async def member_cmd(self, interaction: discord.Interaction, member: str):
        target = await store.find_member(self.bot.db, member)
        if target is None:
            return await interaction.response.send_message(
                embed=embeds.error(f"No record for **{member}**."), ephemeral=True)
        plan = await rolesync.plan_member(self.bot.db, interaction.guild, target)
        if plan.skipped:
            return await interaction.response.send_message(
                embed=embeds.warn(f"**{target.gamertag}** — {plan.skipped}."), ephemeral=True)

        await interaction.response.defer(thinking=True)
        problem = await rolesync.apply_plan(interaction.guild, plan, REASON)
        if problem:
            return await interaction.followup.send(embed=embeds.error(problem))
        if plan.changes:
            await self.bot.audit(interaction.user, "roles synced",
                                 f"{target.gamertag}: +{len(plan.add)} -{len(plan.remove)}")
        e = embeds.base(self.bot.brand, f"🔗  {target.gamertag}", describe(plan))
        await interaction.followup.send(embed=e)

    # ------------------------------------------------------------------ everybody
    @group.command(name="all", description="Bring every member's roles in line (shows a preview first)")
    @admin_only()
    async def all_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        rows = await self.bot.db.all(
            "SELECT * FROM members WHERE active = 1 AND discord_id IS NOT NULL ORDER BY gamertag")
        mapping = await rolesync.achievement_roles(self.bot.db)
        plans, blocked = [], set()
        for row in rows:
            plan = await rolesync.plan_member(
                self.bot.db, interaction.guild,
                store.Member(row["id"], row["discord_id"], row["gamertag"], row["active"]),
                mapping)
            # Collected from every plan, not just the actionable ones: a member
            # whose only outstanding roles are out of reach has nothing to apply
            # and would otherwise be reported as already up to date.
            blocked.update(plan.blocked)
            if plan.changes:
                plans.append(plan)

        adds = sum(len(p.add) for p in plans)
        removes = sum(len(p.remove) for p in plans)
        if not plans:
            return await interaction.followup.send(embed=embeds.success(
                "Everybody's roles already match their record."
                + (f"\n\n🔒 {len(blocked)} role(s) are out of my reach — `/sync check`."
                   if blocked else "")))

        e = embeds.base(
            self.bot.brand, "🔗  Role sync preview",
            f"**{len(plans)}** members need changes: "
            f"**{adds}** roles to add, **{removes}** to remove.\n"
            "_Discord rate-limits role changes, so a big first run can take a few minutes._",
        )
        e.add_field(name="Examples",
                    value=embeds.lines_within(
                        [f"**{p.member.gamertag}** — {describe(p)}" for p in plans[:6]]),
                    inline=False)
        if blocked:
            e.add_field(name="🔒 Out of reach",
                        value=f"{len(blocked)} role(s) sit above me and will be skipped. "
                              "`/sync check` explains how to fix that.", inline=False)

        view = views.Confirm(interaction.user.id, "Apply to everyone",
                             discord.ButtonStyle.success)
        message = await interaction.followup.send(embed=e, view=view, wait=True)
        view.message = message
        await view.wait()
        if not view.result:
            return await message.edit(embed=embeds.warn("Cancelled — no roles were changed."),
                                      view=None)

        async def report(embed: discord.Embed) -> None:
            """A big backfill can outlive the 15-minute interaction token, so the
            progress note falls back to a plain channel message."""
            try:
                await message.edit(embed=embed, view=None)
            except discord.HTTPException:
                try:
                    await interaction.channel.send(interaction.user.mention, embed=embed)
                except discord.HTTPException:
                    pass

        await report(embeds.info(self.bot.brand, f"Syncing {len(plans)} members…"))
        done = problems = 0
        for i, plan in enumerate(plans, 1):
            if await rolesync.apply_plan(interaction.guild, plan, REASON):
                problems += 1
            else:
                done += 1
            if i % 25 == 0:
                await report(embeds.info(self.bot.brand, f"Syncing… **{i}/{len(plans)}**"))

        await self.bot.audit(interaction.user, "roles synced",
                             f"{done} members, +{adds} -{removes}")
        await report(embeds.success(
            f"Synced **{done}** members (+{adds} roles, −{removes})."
            + (f"\n⚠️ {problems} member(s) failed — see `/sync check`." if problems else "")))


async def setup(bot):
    await bot.add_cog(Sync(bot))
