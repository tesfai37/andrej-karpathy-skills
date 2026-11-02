"""Answers the old bot's ! commands with the slash command that replaced them.

It only ever translates. Nothing typed here is run or recorded: an officer who
types the old !add_achievement gets the filled-in /achievement give back, and
still has to send it, where the officer check and the audit log apply."""
from __future__ import annotations

import re
from dataclasses import dataclass

import discord
from discord.ext import commands

from .. import embeds
from ..parsing import ROLE_WORDS

OLD = re.compile(r"^!([a-z_]+)\b\s*(.*)$", re.I | re.S)
BRACKETS = re.compile(r"\[([^\]]*)\]")
GUIDE_COMMANDS = ("cp", "link", "setup", "craft", "farming")
LOOKUPS = {"info": "", "clears": "", "tank": "tank", "healer": "healer", "dps": "dps",
           "parse": ""}


@dataclass
class Translation:
    old: str            # the old command, as they'd recognise it
    command: str        # the slash command that replaced it
    options: str = ""   # filled in as far as the old one allows
    hint: str = ""      # anything that can't be filled in for them

    @property
    def new(self) -> str:
        return f"{self.command} {self.options}".strip()


def translate(content: str) -> Translation | None:
    """The slash command for an old ! command, or None if it isn't one."""
    match = OLD.match(content.strip())
    if not match:
        return None
    name = match.group(1).lower()
    base = name[:-2] if name.endswith("_g") else name    # the _g variants are one command now
    rest = match.group(2).strip()
    args = [a.strip() for a in BRACKETS.findall(rest)] or ([rest] if rest else [])
    arg = args[0] if args else ""
    old = f"!{name}"

    def part(i: int) -> str:
        return args[i] if len(args) > i else ""

    if base in LOOKUPS:
        options = (f"member:{arg} " if arg else "") + (f"role:{LOOKUPS[base]}" if LOOKUPS[base] else "")
        hint = "Then tap 🏆 **Scores & parses** on the card." if base == "parse" else ""
        return Translation(old, "/profile", options.strip(), hint)
    if base in ("add_achievement", "remove_achievement"):
        verb = "give" if base == "add_achievement" else "take"
        role = ROLE_WORDS.get(part(2).lower(), "")
        return Translation(old, f"/achievement {verb}",
                           f"member:{part(1)} role:{role} achievements:{part(0).lower()}",
                           "" if role else "Pick the role from the list.")
    if base == "add_parse":
        return Translation(old, "/record parse",
                           f"member:{part(2)} label:{part(1)} dps:{part(0)}")
    if name == "update_name":
        return Translation(old, "/member link", f"gamertag:{part(0)} user:",
                           f"Then pick **{part(1)}** from the list." if part(1) else "")
    if name == "update_tag":
        return Translation(old, "/member rename",
                           f"gamertag:{part(0)} new_gamertag:{part(1)}")
    if name in GUIDE_COMMANDS:
        return Translation(old, "/guide show", f"topic:{name}/{arg.lower()}" if arg else "")
    if name == "export":
        return Translation(old, "/export")
    if name == "command":
        return Translation(old, "/help")
    return None


class OldCommands(commands.Cog):
    """Old ! commands"""

    def __init__(self, bot):
        self.bot = bot
        # Anyone can type a ! command, so a member can't use this to make the
        # bot spam a channel.
        self.cooldown = commands.CooldownMapping.from_cooldown(1, 20, commands.BucketType.user)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        found = translate(message.content)
        if found is None or self.cooldown.update_rate_limit(message):
            return
        e = embeds.base(
            self.bot.brand, "That command moved",
            f"`{found.old}` is a slash command now. Here it is, filled in:\n"
            f"```\n{found.new}\n```"
            + (f"{found.hint}\n" if found.hint else "")
            + f"Start typing `{found.command}` and Discord gives you a box for each part.\n"
              "_Nothing was changed. This only shows you the new command._")
        try:
            await message.reply(embed=e, mention_author=False)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(OldCommands(bot))
