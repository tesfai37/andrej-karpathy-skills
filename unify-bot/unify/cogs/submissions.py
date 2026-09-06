"""Reads the submissions channel and records achievements automatically.

A post like

    @BUDMAN008 dps @vSS Ice HM @vSS Fire HM

becomes: member BUDMAN008, role DPS, achievements vssice + vssfire (plus vss,
because both of those require it). Nothing is ever dropped silently - if the
bot can't work something out it says so in the channel with a button to fix it."""
from __future__ import annotations

import json

import discord
from discord.ext import commands

from .. import embeds, parsing, store
from ..checks import user_is_admin
from ..config import ROLES, ROLE_EMOJI, ROLE_LABEL
from .mapping import MapRoleView

APPROVE_ID = "unify:sub:approve"
REJECT_ID = "unify:sub:reject"
ROLE_ID = "unify:sub:role"


class SubmissionView(discord.ui.View):
    """Persistent - the buttons keep working after the bot restarts."""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(RoleChoice())   # also lets an approver correct the role

    async def _pending(self, interaction: discord.Interaction):
        return await self.bot.db.one(
            "SELECT * FROM pending_submissions WHERE message_id = ? AND status = 'pending'",
            (interaction.message.id,),
        )

    async def _guard(self, interaction: discord.Interaction):
        if not await user_is_admin(interaction):
            await interaction.response.send_message(
                embed=embeds.error("Only admins can approve submissions."), ephemeral=True)
            return None
        row = await self._pending(interaction)
        if row is None:
            await interaction.response.send_message(
                embed=embeds.warn("This one has already been dealt with."), ephemeral=True)
        return row

    @discord.ui.button(label="Approve", emoji="✅", style=discord.ButtonStyle.success,
                       custom_id=APPROVE_ID)
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button):
        row = await self._guard(interaction)
        if row is None:
            return
        if not row["role"]:
            return await interaction.response.send_message(
                embed=embeds.warn("Pick the role from the menu first."), ephemeral=True)
        cog: Submissions = self.bot.get_cog("Submissions")
        added = await cog.apply(row, interaction.user)
        await self.bot.db.run("UPDATE pending_submissions SET status='approved' WHERE id=?",
                              (row["id"],))
        member = await store.get_member(self.bot.db, row["member_id"])
        await interaction.response.edit_message(
            embed=embeds.success(
                f"Recorded for **{member.gamertag}** ({ROLE_LABEL[row['role']]}): "
                + (", ".join(added) if added else "nothing new - already had it all")
            ),
            view=None,
        )

    @discord.ui.button(label="Reject", emoji="🗑️", style=discord.ButtonStyle.secondary,
                       custom_id=REJECT_ID)
    async def reject(self, interaction: discord.Interaction, _: discord.ui.Button):
        row = await self._guard(interaction)
        if row is None:
            return
        await self.bot.db.run("UPDATE pending_submissions SET status='rejected' WHERE id=?",
                              (row["id"],))
        await interaction.response.edit_message(
            embed=embeds.warn(f"Rejected by {interaction.user.mention}."), view=None)


class RoleChoice(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Which role was this cleared as?",
            custom_id=ROLE_ID,
            options=[discord.SelectOption(label=ROLE_LABEL[r], value=r, emoji=ROLE_EMOJI[r])
                     for r in ROLES],
        )

    async def callback(self, interaction: discord.Interaction):
        bot = self.view.bot
        row = await bot.db.one(
            "SELECT * FROM pending_submissions WHERE message_id = ? AND status = 'pending'",
            (interaction.message.id,))
        if row is None:
            return await interaction.response.send_message(
                embed=embeds.warn("This one has already been dealt with."), ephemeral=True)
        if interaction.user.id != row["submitter_id"] and not await user_is_admin(interaction):
            return await interaction.response.send_message(
                embed=embeds.error("Only the person who posted it (or an admin) can set the role."),
                ephemeral=True)
        await bot.db.run("UPDATE pending_submissions SET role = ? WHERE id = ?",
                         (self.values[0], row["id"]))
        embed = interaction.message.embeds[0]
        embed.set_field_at(0, name="Role", value=ROLE_LABEL[self.values[0]], inline=True)
        await interaction.response.edit_message(embed=embed, view=self.view)


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
                embed=embeds.error("An admin needs to do this one."), ephemeral=True)
        role = interaction.guild.get_role(self.role_ids[0])
        if role is None:
            return await interaction.response.send_message(
                embed=embeds.error("That role no longer exists."), ephemeral=True)
        view = MapRoleView(self.bot, interaction.user.id, role)
        await interaction.response.send_message(embed=await view.build(), view=view,
                                                ephemeral=True)


class Submissions(commands.Cog):
    """Automatic channel reader"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(SubmissionView(self.bot))

    # ------------------------------------------------------------------ listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        channel_id = await self.bot.setting("submissions_channel")
        if not channel_id or message.channel.id != int(channel_id):
            return

        parsed = await parsing.parse_message(self.bot.db, message.content)
        if parsed.is_empty:
            return

        if parsed.unmapped_role_ids:
            names = ", ".join(f"<@&{r}>" for r in parsed.unmapped_role_ids)
            await message.add_reaction("❓")
            await message.reply(
                embed=embeds.warn(
                    f"I don't know what {names} means yet, so I've left this post alone.\n"
                    "An admin can press the button below to teach me - it takes two taps."),
                view=FixMappingView(self.bot, parsed.unmapped_role_ids),
                mention_author=False,
            )
            return

        member = await self.identify(parsed)
        if member is None:
            await message.add_reaction("❓")
            await message.reply(
                embed=embeds.warn(
                    "I couldn't tell who this is for. @mention them, or type their exact "
                    "gamertag. If they're new, an admin can add them with `/member add`."),
                mention_author=False,
            )
            return

        role = parsed.roles[0] if parsed.roles else ""
        mode = await self.bot.setting("submission_mode")

        if role and mode == "auto":
            row = {"member_id": member.id, "role": role, "keys": json.dumps(parsed.keys),
                   "source_url": message.jump_url}
            added = await self.apply(row, message.author)
            await message.add_reaction("✅")
            await message.reply(
                embed=embeds.success(
                    f"**{member.gamertag}** — {ROLE_EMOJI[role]} {ROLE_LABEL[role]}\n"
                    + (", ".join(added) if added else "_already had all of these_")),
                mention_author=False,
            )
            return

        await self.post_for_review(message, member, role, parsed.keys)

    async def identify(self, parsed: parsing.Parsed) -> store.Member | None:
        db = self.bot.db
        for discord_id in parsed.discord_ids:
            found = await store.find_member(db, discord_id)
            if found:
                return found
        for word in parsed.leftover_words:
            found = await store.find_member(db, word)
            if found:
                return found
        return None

    async def post_for_review(self, message, member, role, keys):
        expanded = await store.expand_prerequisites(self.bot.db, keys)
        e = embeds.base(
            self.bot.brand, "📝  Waiting for approval",
            f"From {message.author.mention} — [jump to post]({message.jump_url})",
        )
        e.add_field(name="Role", value=ROLE_LABEL[role] if role else "❗ pick below", inline=True)
        e.add_field(name="Member", value=member.mention, inline=True)
        e.add_field(name="Achievements", value=", ".join(expanded) or "—", inline=False)

        view = SubmissionView(self.bot)
        posted = await message.reply(embed=e, view=view, mention_author=False)
        await self.bot.db.run(
            "INSERT INTO pending_submissions"
            "(message_id, channel_id, source_url, submitter_id, member_id, role, keys) "
            "VALUES(?,?,?,?,?,?,?)",
            (posted.id, posted.channel.id, message.jump_url, message.author.id,
             member.id, role, json.dumps(keys)),
        )
        await message.add_reaction("⏳")

    # ------------------------------------------------------------------ apply
    async def apply(self, row, actor: discord.abc.User) -> list[str]:
        db = self.bot.db
        member = await store.get_member(db, row["member_id"])
        keys = json.loads(row["keys"])
        added = await store.grant(db, member, row["role"], keys, actor.id, str(actor))
        if added:
            await self.bot.audit(
                actor, "achievements recorded",
                f"{member.gamertag} [{row['role']}] +{', '.join(added)}")
            await self.notify(member, row["role"], added)
        return added

    async def notify(self, member: store.Member, role: str, added: list[str]) -> None:
        if not await self.bot.setting("notify_dm") or not member.discord_id:
            return
        user = self.bot.get_user(member.discord_id)
        if user is None:
            return
        names = await self.bot.db.all(
            f"SELECT name FROM achievements WHERE key IN ({','.join('?' * len(added))})", added)
        e = embeds.base(
            self.bot.brand, "🎉  Nice clear!",
            f"Recorded on your **{ROLE_LABEL[role]}** record:\n"
            + "\n".join(f"• {n[0]}" for n in names)
            + "\n\nRun `/me` in the server to see your card.",
        )
        try:
            await user.send(embed=e)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Submissions(bot))
