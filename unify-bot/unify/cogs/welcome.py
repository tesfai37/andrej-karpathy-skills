"""Welcome card when somebody joins."""
from __future__ import annotations

import discord
from discord.ext import commands

from .. import embeds, store

CLAIM_ID = "unify:claim"


class ClaimModal(discord.ui.Modal, title="Claim my gamertag"):
    """The same claim as /register, for people who've never typed a slash command."""

    gamertag = discord.ui.Label(
        text="Your gamertag",
        description="Exactly as it shows in game",
        component=discord.ui.TextInput(min_length=2, max_length=40))
    proof = discord.ui.Label(
        text="A screenshot of your gamertag in game",
        description="An officer checks it before linking you",
        component=discord.ui.FileUpload(max_values=1))

    async def on_submit(self, interaction: discord.Interaction):
        files = self.proof.component.values
        await interaction.client.get_cog("Membership").claim(
            interaction, self.gamertag.component.value, files[0] if files else None)


class ClaimView(discord.ui.View):
    """Persistent: the button keeps working on old welcome cards after a restart."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim my gamertag", emoji="🪪",
                       style=discord.ButtonStyle.primary, custom_id=CLAIM_ID)
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button):
        blocked = await interaction.client.get_cog("Membership").claim_blocked(
            interaction.user.id)
        if blocked is not None:
            return await interaction.response.send_message(embed=blocked, ephemeral=True)
        await interaction.response.send_modal(ClaimModal())


class Welcome(commands.Cog):
    """Welcome"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ClaimView())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel_id = await self.bot.setting("welcome_channel")
        channel = self.bot.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            return
        known = await store.find_member(self.bot.db, member.id)
        e = embeds.base(
            self.bot.brand, f"Welcome to {self.bot.brand.name}, {member.display_name}!",
            f"{member.mention} just joined.\n\n"
            "• `/me` — your achievement card\n"
            "• `/profile` — look anyone up\n"
            "• `/leaderboard` — see where you stand\n"
            "• `/help` — everything else",
        )
        e.set_thumbnail(url=member.display_avatar.url)
        view = None
        if known:
            e.add_field(name="Roster", value=f"Already linked as **{known.gamertag}**.",
                        inline=False)
        elif await self.bot.setting("registration"):
            e.add_field(name="Roster",
                        value="Tap **Claim my gamertag** and send a screenshot of your "
                              "gamertag in game. An officer checks it and links you, so "
                              "your clears get tracked.", inline=False)
            view = ClaimView()
        else:
            e.add_field(name="Roster",
                        value="An officer will add you with `/member add` so your clears "
                              "get tracked.", inline=False)
        await channel.send(embed=e, view=view)


async def setup(bot):
    await bot.add_cog(Welcome(bot))
