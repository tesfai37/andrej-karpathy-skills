"""One screen of setup, plus /config for the details."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import autocomplete, embeds, views
from ..checks import admin_only
from ..config import SETTING_DEFAULTS


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, key: str, placeholder: str, row: int):
        super().__init__(channel_types=[discord.ChannelType.text],
                         placeholder=placeholder, row=row)
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await interaction.client.db.set_setting(self.key, channel.id)
        await self.view.refresh(interaction)


class AdminRolePicker(discord.ui.RoleSelect):
    def __init__(self, row: int):
        super().__init__(placeholder="Which roles count as admins?", max_values=10, row=row)

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.db.set_setting("admin_roles", [r.id for r in self.values])
        await self.view.refresh(interaction)


class SetupView(views.OwnedView):
    def __init__(self, bot, owner_id: int):
        super().__init__(owner_id, timeout=600)
        self.bot = bot
        self.add_item(ChannelPicker("submissions_channel",
                                    "1) Channel where people post achievements", 0))
        self.add_item(ChannelPicker("audit_channel", "2) Channel for the admin log", 1))
        self.add_item(AdminRolePicker(2))

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
            "Four things and you're done. Nothing here needs a keyboard.",
        )
        e.add_field(name="1 · Submissions channel",
                    value=await channel("submissions_channel"), inline=False)
        e.add_field(name="2 · Admin log channel",
                    value=await channel("audit_channel"), inline=False)
        e.add_field(name="3 · Admin roles",
                    value=" ".join(f"<@&{r}>" for r in admin_roles) or "_server admins only_",
                    inline=False)
        e.add_field(name="4 · Achievement roles",
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
                       style=discord.ButtonStyle.primary, row=3)
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

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=3)
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

    group = app_commands.Group(name="config", description="Bot settings")

    @group.command(name="show", description="Everything the bot is currently set to")
    @admin_only()
    async def show(self, interaction: discord.Interaction):
        e = embeds.base(self.bot.brand, "⚙️  Settings")
        for key, (kind, default, description) in SETTING_DEFAULTS.items():
            value = await self.bot.db.get_setting(key, default)
            if kind == "channel":
                shown = f"<#{value}>" if value else "_not set_"
            elif kind == "roles":
                shown = " ".join(f"<@&{r}>" for r in (value or [])) or "_not set_"
            else:
                shown = f"`{value}`" if value != "" else "_not set_"
            e.add_field(name=key, value=f"{shown}\n_{description}_", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

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
