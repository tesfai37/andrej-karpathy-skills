"""One screen of setup, plus /config for the details."""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, reviews, views
from ..checks import admin_only
from ..config import SETTING_DEFAULTS

# /config show, in the order an officer thinks about it. Every setting appears
# exactly once (the tests hold this), so a new one can't go missing from the screen.
GROUPS = {
    "📍 Where things go": ("submissions_channel", "audit_channel", "evidence_channel",
                          "welcome_channel", "reports_channel"),
    "🛡️ Who can do what": ("admin_roles", "recorder_roles", "submission_mode", "viewer_roles"),
    "🙋 What members may ask for": ("registration", "member_submissions"),
    "📋 Logging": ("activity_channel", "problems_channel", "digest_minutes", "briefing_hour",
                  "audit_days"),
    "🎨 Branding": ("guild_name", "brand_color", "logo_url", "mark_label"),
    "✨ Extras": ("notify_dm", "role_sync", "report_day", "report_hour"),
}
MUST_SET = {"submissions_channel", "audit_channel"}


def shown(key: str, kind: str, value) -> str:
    """One setting's value, the way a person would say it."""
    if kind == "bool":
        return "✅ on" if value else "❌ off"
    if kind == "int":                       # 0 is a real value: midnight, or "forever"
        return f"`{value}`"
    if not value:
        return "⚠️ **not set**" if key in MUST_SET else "_not set_"
    if kind == "channel":
        return f"<#{value}>"
    if kind == "roles":
        more = f" +{len(value) - 5} more" if len(value) > 5 else ""
        return " ".join(f"<@&{r}>" for r in value[:5]) + more
    return f"`{value}`"


# What the bot has to be able to do in the channel it reads posts from.
CHANNEL_NEEDS = {
    "view_channel": "see it",
    "read_message_history": "read its history",
    "send_messages": "reply there",
    "add_reactions": "react to posts",
    "embed_links": "post cards",
    "attach_files": "copy screenshots onto cards",
}


async def status_report(bot, guild: discord.Guild) -> discord.Embed:
    """The live setup in plain English, and anything that's quietly broken."""
    db = bot.db

    def roles(ids):
        return " ".join(f"<@&{r}>" for r in ids)

    async def where(key):
        cid = await bot.setting(key)
        if not cid:
            return "_not set_"
        return f"<#{cid}>" if bot.get_channel(int(cid)) else "⚠️ a deleted channel"

    officers = await bot.setting("admin_roles") or []
    recorders = await bot.setting("recorder_roles") or []
    viewers = await bot.setting("viewer_roles") or []
    mode = await bot.setting("submission_mode")
    fix = []

    e = embeds.base(bot.brand, "🩺  How the bot is set up right now")

    lines = ["**Officers:** " + (f"{roles(officers)}, plus anyone with Manage Server"
                                 if officers else "anyone with Manage Server")]
    if recorders:
        lines.append(f"**Recorders:** {roles(recorders)}. They record for other people, "
                     "never for themselves.")
    else:
        lines.append("**Recorders:** none, so only officers record without a review.")
    if mode == "review":
        lines.append("Right now **every** post waits for an officer, even a recorder's.")
    else:
        lines.append("Officers' and recorders' posts are recorded straight away, except "
                     "their own clears. Everyone else's wait in `/queue`.")
    e.add_field(name="Who can record", value="\n".join(lines), inline=False)

    on = {True: "✅", False: "❌"}
    e.add_field(name="What members can do", value="\n".join([
        f"{on[bool(await bot.setting('registration'))]} Claim their gamertag with "
        "`/register` (an officer approves each one)",
        f"{on[bool(await bot.setting('member_submissions'))]} Send scores and parses with "
        "`/submit` (an officer approves each one)",
        "👀 Look people up: " + (f"only {roles(viewers)}" if viewers else "everyone"),
    ]), inline=False)

    problems = await where("problems_channel")
    if problems == "_not set_" and await bot.setting("audit_channel"):
        problems = "the admin log, since no problems channel is set"
    evidence = await where("evidence_channel")
    if evidence == "_not set_" and await bot.setting("audit_channel"):
        evidence = "the admin log, since no evidence channel is set"
    e.add_field(name="Where things go", value="\n".join([
        f"Achievement posts are read in {await where('submissions_channel')}",
        f"Admin log, and cards for `/register` and `/submit`: {await where('audit_channel')}",
        f"Copies of the screenshots behind instant recordings: {evidence}",
        f"Activity digest, every {await bot.setting('digest_minutes')} min: "
        f"{await where('activity_channel')}",
        f"Problems, and the {await bot.setting('briefing_hour')}:00 UTC briefing: {problems}",
    ]), inline=False)

    waiting = await reviews.pending_count(db)
    roster = await db.val("SELECT COUNT(*) FROM members WHERE active = 1", (), 0)
    unlinked = await db.val(
        "SELECT COUNT(*) FROM members WHERE active = 1 AND discord_id IS NULL", (), 0)
    achievements = await db.val("SELECT COUNT(*) FROM achievements", (), 0)
    mapped = await db.val(
        "SELECT COUNT(*) FROM achievements WHERE key IN "
        "(SELECT value FROM role_map WHERE kind = 'achievement')", (), 0)
    copies = sorted(bot.db.backup_folder.glob("*.sqlite3"), key=lambda f: f.stat().st_mtime)
    newest = (datetime.fromtimestamp(copies[-1].stat().st_mtime, timezone.utc)
              .strftime("%Y-%m-%d %H:%M") if copies else "")
    e.add_field(name="Right now", value="\n".join([
        f"📥 **{waiting}** waiting in `/queue`",
        f"👥 **{roster}** on the roster, **{unlinked}** not linked to a Discord account",
        f"🔗 **{mapped}** of **{achievements}** achievements have a Discord role",
        f"💾 Last backup **{newest} UTC**, {len(copies)} kept in `data/backups`" if copies
        else "💾 No backup yet. One is saved every day, and before every import.",
    ]), inline=False)

    channel_id = await bot.setting("submissions_channel")
    channel = bot.get_channel(int(channel_id)) if channel_id else None
    if not channel_id:
        fix.append("No submissions channel, so nobody's posts are read. Pick one in `/setup`.")
    elif channel is None:
        fix.append("The submissions channel was deleted. Pick a new one in `/setup`.")
    else:
        mine = channel.permissions_for(guild.me)
        missing = [why for perm, why in CHANNEL_NEEDS.items() if not getattr(mine, perm)]
        if missing:
            fix.append(f"In {channel.mention} I can't " + ", ".join(missing)
                       + ". Fix my role's permissions there.")
    if roster and unlinked == roster:
        fix.append("Nobody on the roster is linked to a Discord account yet, so posts that "
                   "@mention people can't find anyone. Run `/member match` to link "
                   "everybody from their old Discord names.")
    if not (await bot.setting("evidence_channel") or await bot.setting("audit_channel")):
        fix.append("There's nowhere to keep copies of screenshots, so nothing records "
                   "straight away: every post waits for an officer. Set an admin log "
                   "channel in `/setup`.")
    if not (await bot.setting("audit_channel") or await bot.setting("problems_channel")):
        fix.append("No admin log or problems channel, so `/register` and `/submit` cards "
                   "only show in `/queue` and there's no morning briefing.")
    if guild.id in {int(r) for r in officers}:
        fix.append("@everyone is an officer role, so anyone can change anything. "
                   "Remove it with `/config roles`.")
    if guild.id in {int(r) for r in recorders}:
        fix.append("@everyone is a recorder role, so anyone's post records for other "
                   "people. Remove it with `/config roles`.")
    if await bot.setting("role_sync") and not guild.me.guild_permissions.manage_roles:
        fix.append("Role sync is on, but I don't have Manage Roles, so nobody gets their roles.")
    e.add_field(name="⚠️ Fix these" if fix else "Problems",
                value="\n".join(f"• {line}" for line in fix) if fix else "✅ Nothing needs fixing.",
                inline=False)
    return e


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, key: str, placeholder: str, row: int):
        super().__init__(channel_types=[discord.ChannelType.text],
                         placeholder=placeholder, row=row)
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await interaction.client.db.set_setting(self.key, channel.id)
        await self.view.refresh(interaction)


class RolesPicker(discord.ui.RoleSelect):
    def __init__(self, key: str, placeholder: str, row: int):
        super().__init__(placeholder=placeholder, max_values=10, row=row)
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.db.set_setting(self.key, [r.id for r in self.values])
        await self.view.refresh(interaction)


class SetupView(views.OwnedView):
    def __init__(self, bot, owner_id: int):
        super().__init__(owner_id, timeout=600)
        self.bot = bot
        self.add_item(ChannelPicker("submissions_channel",
                                    "1) Channel where people post achievements", 0))
        self.add_item(ChannelPicker("audit_channel", "2) Channel for the admin log", 1))
        self.add_item(RolesPicker("admin_roles", "3) Which roles are officers?", 2))
        self.add_item(RolesPicker("recorder_roles",
                                  "4) Which roles may record for others? (raid leads)", 3))

    async def build(self) -> discord.Embed:
        db = self.bot.db

        async def channel(key):
            cid = await db.get_setting(key, 0)
            return f"<#{cid}>" if cid else "_not set_"

        admin_roles = await db.get_setting("admin_roles", []) or []
        mapped = await db.val("SELECT COUNT(*) FROM role_map", (), 0)
        members = await db.val("SELECT COUNT(*) FROM members WHERE active = 1", (), 0)

        e = embeds.base(
            self.bot.brand, "🚀  Set up the bot",
            "Five things and you're done. Nothing here needs a keyboard.\n"
            "Activity and problem log channels are set with `/config channel`.",
        )
        e.add_field(name="1 · Submissions channel",
                    value=await channel("submissions_channel"), inline=False)
        e.add_field(name="2 · Admin log channel",
                    value=await channel("audit_channel"), inline=False)
        e.add_field(name="3 · Officer roles",
                    value=" ".join(f"<@&{r}>" for r in admin_roles) or "_server admins only_",
                    inline=False)
        recorders = await db.get_setting("recorder_roles", []) or []
        e.add_field(
            name="4 · Recorder roles",
            value=(" ".join(f"<@&{r}>" for r in recorders) +
                   "\n_these may record for other people, never for themselves_")
                  if recorders else
                  "_none yet — without these, every submission waits for an officer_",
            inline=False)
        e.add_field(name="5 · Achievement roles",
                    value=(f"**{mapped}** roles mapped — press the button below to scan for more"
                           if mapped else
                           "None yet. Press **Auto-map roles** and I'll match your existing "
                           "Discord roles to achievements by name."),
                    inline=False)
        e.add_field(name="Roster", value=(f"**{members}** members. "
                                          "Drop your spreadsheet in with `/import`."
                                          if members else
                                          "Empty. Run `/import` and drag your spreadsheet in, "
                                          "or add people one at a time with `/member add`."),
                    inline=False)
        return e

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=await self.build(), view=self)

    @discord.ui.button(label="Auto-map roles", emoji="🔗",
                       style=discord.ButtonStyle.primary, row=4)
    async def automap(self, interaction: discord.Interaction, _: discord.ui.Button):
        cog = self.bot.get_cog("Mapping")
        found = await cog.propose(interaction.guild)
        if not found:
            return await interaction.response.send_message(
                embed=embeds.warn("No new roles matched by name. Use `/map role` for those — "
                                  "it's two taps each."), ephemeral=True)
        await self.bot.db.run_many([
            ("INSERT INTO role_map(discord_role_id, kind, value, label) VALUES(?,?,?,?) "
             "ON CONFLICT(discord_role_id) DO UPDATE SET kind=excluded.kind, "
             "value=excluded.value, label=excluded.label",
             (role.id, kind, value, role.name))
            for role, kind, value, _ in found
        ])
        await self.bot.audit(interaction.user, "role mapping",
                             f"auto-mapped {len(found)} roles from /setup")
        await interaction.response.send_message(
            embed=embeds.success(f"Mapped {len(found)} roles. `/map list` shows them all, "
                                 "`/map missing` shows what's left."),
            ephemeral=True)

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=4)
    async def done(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.clear_items()
        await interaction.response.edit_message(
            embed=embeds.success("Setup saved. Try `/help` next — and post an achievement in "
                                 "your submissions channel to see it work."),
            view=None)
        self.stop()


class Settings(commands.Cog):
    """Setup & config"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Set the bot up - start here")
    @admin_only()
    async def setup(self, interaction: discord.Interaction):
        view = SetupView(self.bot, interaction.user.id)
        await interaction.response.send_message(embed=await view.build(), view=view,
                                                ephemeral=True)

    @app_commands.command(description="How the bot is set up right now, in plain English")
    @admin_only()
    async def status(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=await status_report(self.bot, interaction.guild), ephemeral=True)

    group = app_commands.Group(name="config", description="Bot settings")

    @group.command(name="show", description="Everything the bot is currently set to")
    @admin_only()
    async def show(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=await self.settings_screen(),
                                                ephemeral=True)

    async def settings_screen(self) -> discord.Embed:
        e = embeds.base(self.bot.brand, "⚙️  Settings",
                        "Change one with `/config set`, or `/config channel` and "
                        "`/config roles` to pick from a list. `/status` explains what "
                        "they add up to.")
        for title, keys in GROUPS.items():
            lines = []
            for key in keys:
                kind, default, description = SETTING_DEFAULTS[key]
                value = await self.bot.db.get_setting(key, default)
                lines.append(f"**{key}**: {shown(key, kind, value)}\n_{description}_")
            e.add_field(name=title, value=embeds.lines_within(lines), inline=False)
        return e

    @group.command(name="set", description="Change a setting")
    @app_commands.autocomplete(key=autocomplete.settings_keys)
    @admin_only()
    async def set_cmd(self, interaction: discord.Interaction, key: str, value: str):
        meta = SETTING_DEFAULTS.get(key)
        if meta is None:
            return await interaction.response.send_message(
                embed=embeds.error(f"`{key}` isn't a setting. `/config show` lists them."),
                ephemeral=True)
        kind = meta[0]
        try:
            parsed = parse_value(kind, value)
        except ValueError as exc:
            return await interaction.response.send_message(embed=embeds.error(str(exc)),
                                                           ephemeral=True)
        await self.bot.db.set_setting(key, parsed)
        await self.bot.refresh_brand()
        await self.bot.audit(interaction.user, "setting changed", f"{key} = {parsed}")
        await interaction.response.send_message(
            embed=embeds.success(f"`{key}` is now `{parsed}`."), ephemeral=True)

    @group.command(name="channel", description="Point a setting at a channel")
    @app_commands.choices(key=[
        app_commands.Choice(name=k, value=k)
        for k, meta in SETTING_DEFAULTS.items() if meta[0] == "channel"
    ])
    @admin_only()
    async def channel_cmd(self, interaction: discord.Interaction, key: app_commands.Choice[str],
                          channel: discord.TextChannel):
        await self.bot.db.set_setting(key.value, channel.id)
        await self.bot.audit(interaction.user, "setting changed",
                             f"{key.value} = #{channel.name}")
        await interaction.response.send_message(
            embed=embeds.success(f"`{key.value}` is now {channel.mention}."), ephemeral=True)

    @group.command(name="roles", description="Add or remove a role from admin/viewer access")
    @app_commands.choices(
        key=[app_commands.Choice(name=k, value=k)
             for k, meta in SETTING_DEFAULTS.items() if meta[0] == "roles"],
        action=[app_commands.Choice(name="Add", value="add"),
                app_commands.Choice(name="Remove", value="remove")])
    @admin_only()
    async def roles_cmd(self, interaction: discord.Interaction, key: app_commands.Choice[str],
                        action: app_commands.Choice[str], role: discord.Role):
        current = set(await self.bot.db.get_setting(key.value, []) or [])
        current.add(role.id) if action.value == "add" else current.discard(role.id)
        await self.bot.db.set_setting(key.value, sorted(current))
        await self.bot.audit(interaction.user, "setting changed",
                             f"{key.value} {action.value} @{role.name}")
        await interaction.response.send_message(
            embed=embeds.success(
                f"`{key.value}`: " + (" ".join(f"<@&{r}>" for r in current) or "_empty_")),
            ephemeral=True)


def parse_value(kind: str, raw: str):
    raw = raw.strip()
    if kind == "bool":
        if raw.lower() in {"true", "yes", "on", "1"}:
            return True
        if raw.lower() in {"false", "no", "off", "0"}:
            return False
        raise ValueError("Type `true` or `false`.")
    if kind == "int":
        if not raw.lstrip("-").isdigit():
            raise ValueError("That needs to be a whole number.")
        return int(raw)
    if kind == "color":
        try:
            int(raw.lstrip("#"), 16)
        except ValueError:
            raise ValueError("Use a hex colour like `#8B5CF6`.")
        return raw
    if kind == "channel":
        digits = raw.strip("<#>")
        if not digits.isdigit():
            raise ValueError("Use `/config channel` instead — it lets you pick from a list.")
        return int(digits)
    if kind.startswith("choice:"):
        allowed = kind.split(":", 1)[1].split(",")
        if raw.lower() not in allowed:
            raise ValueError("Pick one of: " + ", ".join(f"`{a}`" for a in allowed))
        return raw.lower()
    if kind == "roles":
        raise ValueError("Use `/config roles` instead — it lets you pick from a list.")
    return raw


async def setup(bot):
    await bot.add_cog(Settings(bot))
