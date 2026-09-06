"""Admin commands. Every one of them writes to the audit log and can be undone."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, store, views
from ..checks import admin_only
from ..config import ROLES, ROLE_LABEL

ROLE_CHOICES = [app_commands.Choice(name=ROLE_LABEL[r], value=r) for r in ROLES] + [
    app_commands.Choice(name="Account Wide", value="account")
]


async def need_member(interaction: discord.Interaction, query: str) -> store.Member | None:
    found = await store.find_member(interaction.client.db, query)
    if found is None:
        await interaction.response.send_message(
            embed=embeds.error(
                f"No record for **{query}**. Add them first with `/member add`."),
            ephemeral=True)
    return found


class Admin(commands.Cog):
    """Admin"""

    def __init__(self, bot):
        self.bot = bot

    member_group = app_commands.Group(name="member", description="Add and edit people")
    ach_group = app_commands.Group(name="achievement", description="Give and take achievements")
    trial_group = app_commands.Group(name="trial", description="Manage the list of trials")
    record_group = app_commands.Group(name="record", description="Record scores and parses")

    # ------------------------------------------------------------------ members
    @member_group.command(name="add", description="Add somebody to the roster")
    @app_commands.describe(gamertag="Their in-game name", user="Their Discord account (optional)")
    @admin_only()
    async def member_add(self, interaction: discord.Interaction, gamertag: str,
                         user: discord.Member | None = None):
        result = await store.upsert_member(self.bot.db, gamertag, user.id if user else None)
        member, created = result.member, result.created
        entry = await store.log_action(
            self.bot.db, interaction.user.id, str(interaction.user), "member add",
            f"{member.gamertag}",
            undo=[("DELETE FROM members WHERE id = ?", (member.id,))] if created else [],
        )
        await self.bot.audit(interaction.user, "member added" if created else "member updated",
                             member.gamertag, entry)
        if result.conflict:
            return await interaction.response.send_message(embed=embeds.warn(
                f"**{member.gamertag}** is on the roster, but {result.conflict}.\n"
                "Nothing was overwritten. Use `/member link` or `/member rename` if you "
                "meant to move the account."))
        await interaction.response.send_message(embed=embeds.success(
            f"**{member.gamertag}** is {'now on' if created else 'already on'} the roster"
            + (f" and linked to {user.mention}." if user else ".")))

    @member_group.command(name="link", description="Connect a gamertag to a Discord account")
    @app_commands.autocomplete(gamertag=autocomplete.members)
    @admin_only()
    async def member_link(self, interaction: discord.Interaction, gamertag: str,
                          user: discord.Member):
        member = await need_member(interaction, gamertag)
        if member is None:
            return
        before = member.discord_id
        await self.bot.db.run("UPDATE members SET discord_id = ? WHERE id = ?",
                              (user.id, member.id))
        entry = await store.log_action(
            self.bot.db, interaction.user.id, str(interaction.user), "member link",
            f"{member.gamertag} -> {user}",
            undo=[("UPDATE members SET discord_id = ? WHERE id = ?", (before, member.id))])
        await self.bot.audit(interaction.user, "member linked",
                             f"{member.gamertag} -> {user}", entry)
        await interaction.response.send_message(
            embed=embeds.success(f"**{member.gamertag}** is now {user.mention}."))

    @member_group.command(name="rename", description="Change somebody's gamertag")
    @app_commands.autocomplete(gamertag=autocomplete.members)
    @admin_only()
    async def member_rename(self, interaction: discord.Interaction, gamertag: str,
                            new_gamertag: str):
        member = await need_member(interaction, gamertag)
        if member is None:
            return
        await self.bot.db.run("UPDATE members SET gamertag = ? WHERE id = ?",
                              (new_gamertag.strip(), member.id))
        entry = await store.log_action(
            self.bot.db, interaction.user.id, str(interaction.user), "member rename",
            f"{member.gamertag} -> {new_gamertag}",
            undo=[("UPDATE members SET gamertag = ? WHERE id = ?", (member.gamertag, member.id))])
        await self.bot.audit(interaction.user, "member renamed",
                             f"{member.gamertag} -> {new_gamertag}", entry)
        await interaction.response.send_message(
            embed=embeds.success(f"**{member.gamertag}** is now **{new_gamertag}**."))

    @member_group.command(name="remove", description="Take somebody off the roster")
    @app_commands.autocomplete(gamertag=autocomplete.members)
    @admin_only()
    async def member_remove(self, interaction: discord.Interaction, gamertag: str):
        member = await need_member(interaction, gamertag)
        if member is None:
            return
        view = views.Confirm(interaction.user.id, "Remove")
        await interaction.response.send_message(
            embed=embeds.warn(
                f"Remove **{member.gamertag}** from the roster?\n"
                "Their achievements are kept, they just stop showing up in lists. "
                "`/undo` puts them back."),
            view=view, ephemeral=True)
        await view.wait()
        if not view.result:
            return await interaction.edit_original_response(
                embed=embeds.info(self.bot.brand, "Cancelled."), view=None)
        await self.bot.db.run("UPDATE members SET active = 0 WHERE id = ?", (member.id,))
        entry = await store.log_action(
            self.bot.db, interaction.user.id, str(interaction.user), "member remove",
            member.gamertag,
            undo=[("UPDATE members SET active = 1 WHERE id = ?", (member.id,))])
        await self.bot.audit(interaction.user, "member removed", member.gamertag, entry)
        await interaction.edit_original_response(
            embed=embeds.success(f"**{member.gamertag}** removed."), view=None)

    # ------------------------------------------------------------------ grants
    @ach_group.command(name="give", description="Give somebody an achievement")
    @app_commands.describe(achievements="One or more - prerequisites are filled in automatically")
    @app_commands.choices(role=ROLE_CHOICES)
    @app_commands.autocomplete(member=autocomplete.members, achievements=autocomplete.achievements)
    @admin_only()
    async def give(self, interaction: discord.Interaction, member: str,
                   role: app_commands.Choice[str], achievements: str):
        target = await need_member(interaction, member)
        if target is None:
            return
        keys, unknown = await self.split_keys(achievements)
        if not keys:
            return await interaction.response.send_message(
                embed=embeds.error(f"I don't recognise: {', '.join(unknown)}"), ephemeral=True)
        added = await store.grant(self.bot.db, target, role.value, keys,
                                  interaction.user.id, str(interaction.user))
        entry = await self.bot.db.val("SELECT MAX(id) FROM audit_log") if added else None
        if added:
            await self.bot.sync_roles(interaction.guild, target, "achievements given")
        await self.bot.audit(interaction.user, "achievements given",
                             f"{target.gamertag} [{role.value}] +{', '.join(added) or 'nothing'}",
                             entry)
        note = f"\n_Ignored: {', '.join(unknown)}_" if unknown else ""
        await interaction.response.send_message(embed=embeds.success(
            f"**{target.gamertag}** ({role.name}): "
            + (", ".join(added) if added else "already had all of these") + note))

    @ach_group.command(name="take", description="Remove an achievement from somebody")
    @app_commands.choices(role=ROLE_CHOICES)
    @app_commands.autocomplete(member=autocomplete.members, achievements=autocomplete.achievements)
    @admin_only()
    async def take(self, interaction: discord.Interaction, member: str,
                   role: app_commands.Choice[str], achievements: str):
        target = await need_member(interaction, member)
        if target is None:
            return
        keys, unknown = await self.split_keys(achievements)
        removed = await store.revoke(self.bot.db, target, role.value, keys,
                                     interaction.user.id, str(interaction.user))
        entry = await self.bot.db.val("SELECT MAX(id) FROM audit_log") if removed else None
        if removed:
            await self.bot.sync_roles(interaction.guild, target, "achievements removed")
        await self.bot.audit(interaction.user, "achievements removed",
                             f"{target.gamertag} [{role.value}] -{', '.join(removed) or 'nothing'}",
                             entry)
        await interaction.response.send_message(embed=embeds.success(
            f"**{target.gamertag}** ({role.name}): removed "
            + (", ".join(removed) if removed else "nothing - they didn't have those")))

    async def split_keys(self, text: str) -> tuple[list[str], list[str]]:
        known = {r[0] for r in await self.bot.db.all("SELECT key FROM achievements")}
        words = text.replace(",", " ").split()
        return [w for w in words if w in known], [w for w in words if w not in known]

    # ------------------------------------------------------------------ numbers
    @record_group.command(name="score", description="Record a trial score")
    @app_commands.autocomplete(member=autocomplete.members, trial=autocomplete.trials)
    @admin_only()
    async def score_set(self, interaction: discord.Interaction, member: str, trial: str,
                        score: app_commands.Range[int, 0, 1_000_000]):
        target = await need_member(interaction, member)
        if target is None:
            return
        if not await self.bot.db.one("SELECT 1 FROM trials WHERE key = ?", (trial,)):
            return await interaction.response.send_message(
                embed=embeds.error(f"`{trial}` isn't a trial I know."), ephemeral=True)
        await store.set_score(self.bot.db, target, trial, score,
                              interaction.user.id, str(interaction.user))
        entry = await self.bot.db.val("SELECT MAX(id) FROM audit_log")
        await self.bot.audit(interaction.user, "score recorded",
                             f"{target.gamertag} {trial} = {score:,}", entry)
        await interaction.response.send_message(embed=embeds.success(
            f"**{target.gamertag}** — {trial} score set to **{score:,}**."))

    @trial_group.command(name="add", description="Add a brand new trial to the bot")
    @app_commands.describe(key="Short code, e.g. vox", short="Label in lists, e.g. vOX",
                           name="Full name", emoji="One emoji (optional)")
    @admin_only()
    async def trial_add(self, interaction: discord.Interaction, key: str, short: str,
                        name: str, emoji: str = ""):
        key = key.strip().lower()
        if await self.bot.db.one("SELECT 1 FROM trials WHERE key = ?", (key,)):
            return await interaction.response.send_message(
                embed=embeds.error(f"`{key}` already exists."), ephemeral=True)
        icon = embeds.as_emoji(emoji)
        if emoji.strip() and icon is None:
            return await interaction.response.send_message(
                embed=embeds.error(
                    f"`{emoji.strip()}` isn't an emoji I can use. Paste a real one "
                    "(🐉) or one of your server's custom emoji, or leave it blank."),
                ephemeral=True)
        top = await self.bot.db.val("SELECT COALESCE(MAX(sort),0) FROM trials WHERE key<>'account'", (), 0)
        await self.bot.db.run(
            "INSERT INTO trials(key, name, short, emoji, sort) VALUES(?,?,?,?,?)",
            (key, name.strip(), short.strip(), icon or "", top + 10))
        await self.bot.audit(interaction.user, "trial added", f"{short} ({key})")
        await interaction.response.send_message(embed=embeds.success(
            f"Added **{name}**. Now add its achievements with `/achievement new`."))

    @ach_group.command(name="new", description="Add a new achievement to a trial")
    @app_commands.describe(key="Short code, e.g. voxhm", name="What people should see",
                           requires="Codes that must come with it, space separated (optional)")
    @app_commands.choices(kind=[
        app_commands.Choice(name="Trial clear", value="clear"),
        app_commands.Choice(name="Boss hard mode", value="boss"),
        app_commands.Choice(name="Full hard mode", value="hm"),
        app_commands.Choice(name="Title", value="title"),
    ])
    @app_commands.autocomplete(trial=autocomplete.trials)
    @admin_only()
    async def ach_new(self, interaction: discord.Interaction, trial: str, key: str, name: str,
                      kind: app_commands.Choice[str], requires: str = ""):
        key = key.strip().lower()
        if await self.bot.db.one("SELECT 1 FROM achievements WHERE key = ?", (key,)):
            return await interaction.response.send_message(
                embed=embeds.error(f"`{key}` already exists."), ephemeral=True)
        if not await self.bot.db.one("SELECT 1 FROM trials WHERE key = ?", (trial,)):
            return await interaction.response.send_message(
                embed=embeds.error(f"`{trial}` isn't a trial. Add it with `/trial add` first."),
                ephemeral=True)
        wanted, unknown = await self.split_keys(requires)
        top = await self.bot.db.val(
            "SELECT COALESCE(MAX(sort),0) FROM achievements WHERE trial_key = ?", (trial,), 0)
        await self.bot.db.run(
            "INSERT INTO achievements(key, name, trial_key, kind, sort, requires) "
            "VALUES(?,?,?,?,?,?)",
            (key, name.strip(), trial, kind.value, top + 1, " ".join(wanted)))
        await self.bot.audit(interaction.user, "achievement added", f"{name} ({key})")
        note = (f"\n\n⚠️ I ignored `{'`, `'.join(unknown)}` in **requires** - "
                "no achievement has that code." if unknown else "")
        await interaction.response.send_message(embed=embeds.success(
            f"Added **{name}**. Link a Discord role to it with `/map role`." + note))

    @ach_group.command(name="rename", description="Change how an achievement is displayed")
    @app_commands.autocomplete(achievement=autocomplete.achievements)
    @admin_only()
    async def ach_rename(self, interaction: discord.Interaction, achievement: str, name: str):
        row = await self.bot.db.one("SELECT * FROM achievements WHERE key = ?", (achievement,))
        if row is None:
            return await interaction.response.send_message(
                embed=embeds.error(f"`{achievement}` isn't an achievement I know."), ephemeral=True)
        await self.bot.db.run("UPDATE achievements SET name = ? WHERE key = ?",
                              (name.strip(), achievement))
        await self.bot.audit(interaction.user, "achievement renamed",
                             f"{row['name']} -> {name}")
        await interaction.response.send_message(
            embed=embeds.success(f"`{achievement}` is now shown as **{name}**."))

    @record_group.command(name="parse", description="Record a parse")
    @app_commands.describe(label="What was parsed, e.g. '3m dummy'", dps="Damage per second")
    @app_commands.autocomplete(member=autocomplete.members, label=autocomplete.parse_labels)
    @admin_only()
    async def parse_set(self, interaction: discord.Interaction, member: str, label: str,
                        dps: app_commands.Range[int, 0, 500_000]):
        target = await need_member(interaction, member)
        if target is None:
            return
        await store.set_parse(self.bot.db, target, label.strip(), dps,
                              interaction.user.id, str(interaction.user))
        entry = await self.bot.db.val("SELECT MAX(id) FROM audit_log")
        await self.bot.audit(interaction.user, "parse recorded",
                             f"{target.gamertag} {label} = {dps:,}", entry)
        await interaction.response.send_message(embed=embeds.success(
            f"**{target.gamertag}** — {label}: **{dps:,}**."))

    # ------------------------------------------------------------------ safety net
    @app_commands.command(description="Reverse the last change (or a specific audit entry)")
    @app_commands.describe(entry="Audit number from /audit - leave blank for the last change")
    @admin_only()
    async def undo(self, interaction: discord.Interaction, entry: int | None = None):
        if entry is None:
            entry = await self.bot.db.val(
                "SELECT id FROM audit_log WHERE undone = 0 AND undo NOT IN ('', '[]') "
                "ORDER BY id DESC LIMIT 1")
        if entry is None:
            return await interaction.response.send_message(
                embed=embeds.warn("There's nothing to undo."), ephemeral=True)
        summary = await store.undo_action(self.bot.db, int(entry))
        if summary is None:
            return await interaction.response.send_message(
                embed=embeds.warn(f"Audit #{entry} can't be undone (already reversed, or "
                                  "it wasn't a change)."), ephemeral=True)
        await self.bot.audit(interaction.user, "undo", f"#{entry} — {summary}")
        await interaction.response.send_message(
            embed=embeds.success(f"Reversed audit #{entry}: {summary}"))

    @app_commands.command(description="Recent admin activity")
    @admin_only()
    async def audit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 200] = 40):
        rows = await self.bot.db.all(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        if not rows:
            return await interaction.response.send_message(
                embed=embeds.warn("Nothing logged yet."), ephemeral=True)
        lines = [
            f"`#{r['id']}` **{r['action']}** — {r['summary']}\n"
            f"　　_{r['actor_name']} · {r['ts']}_" + ("  ↩️ undone" if r["undone"] else "")
            for r in rows
        ]
        pages = [
            embeds.base(self.bot.brand, "📜  Audit log", "\n".join(lines[i:i + 8]))
            for i in range(0, len(lines), 8)
        ]
        view = views.Paginator(interaction.user.id, pages)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
