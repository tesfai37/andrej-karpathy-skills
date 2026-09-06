"""Everything that turns rows into something pretty. Mobile-first: short lines,
no wide code blocks, bars instead of tables."""
from __future__ import annotations

from dataclasses import dataclass

import discord

from .config import ROLE_EMOJI, ROLE_LABEL

FULL, EMPTY = "▰", "▱"
TICK, CROSS, MARK = "✅", "⬜", "🅛"
KIND_ICON = {"clear": "🔹", "boss": "🔸", "hm": "🔥", "title": "🏅"}

FIELD_LIMIT = 1024          # Discord's cap on an embed field value


def as_emoji(text: str | None) -> str | None:
    """Return `text` only if Discord will accept it as an emoji, else None.

    Trials and achievements are editable from Discord, so somebody will
    eventually type a word into an emoji box. Left unchecked that word is sent
    as a unicode emoji name and the API rejects the whole message - which would
    break /profile for the entire server."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = discord.PartialEmoji.from_str(text)
    except Exception:
        return None
    if parsed.id is not None:               # custom server emoji, <:name:id>
        return text
    if any(c.isascii() and c.isalnum() for c in text) or len(text) > 8:
        return None                          # a word, not an emoji
    return text


def lines_within(lines: list[str], limit: int = FIELD_LIMIT) -> str:
    """Join as many lines as fit, then say how many were left out."""
    out, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit - 24:
            out.append(f"…and {len(lines) - i} more")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


@dataclass
class Brand:
    name: str = "Unify"
    color: int = 0x8B5CF6
    logo: str = ""

    @classmethod
    def from_settings(cls, name: str, color: str, logo: str) -> "Brand":
        try:
            value = int(str(color).lstrip("#"), 16)
        except ValueError:
            value = 0x8B5CF6
        return cls(name or "Unify", value, logo or "")


def bar(done: int, total: int, width: int = 6) -> str:
    if total <= 0:
        return EMPTY * width
    filled = min(width, round(width * done / total))
    if done and filled == 0:
        filled = 1
    return FULL * filled + EMPTY * (width - filled)


def base(brand: Brand, title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=brand.color)
    if brand.logo:
        e.set_thumbnail(url=brand.logo)
    e.set_footer(text=brand.name)
    return e


def info(brand: Brand, text: str) -> discord.Embed:
    return discord.Embed(description=f"ℹ️ {text}", color=brand.color)


def success(text: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {text}", color=0x22C55E)


def warn(text: str) -> discord.Embed:
    return discord.Embed(description=f"⚠️ {text}", color=0xF59E0B)


def error(text: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {text}", color=0xEF4444)


def profile_embed(
    brand: Brand,
    gamertag: str,
    discord_id: int | None,
    role: str,
    rows: list[dict],
    member_titles: list[str],
    score_total: int,
    points_total: int,
    avatar_url: str | None = None,
) -> discord.Embed:
    """Summary card: only the trials the member has actually touched get a bar."""
    started = [r for r in rows if r["done"]]
    untouched = [r for r in rows if not r["done"]]
    done_sum = sum(r["done"] for r in started)
    total_sum = sum(r["total"] for r in rows)

    e = discord.Embed(
        title=f"{ROLE_EMOJI[role]}  {gamertag} — {ROLE_LABEL[role]}",
        color=brand.color,
        description=(
            f"{bar(done_sum, total_sum, 12)}  **{done_sum}/{total_sum}** achievements\n"
            + (f"<@{discord_id}>  •  " if discord_id else "")
            + f"⭐ **{points_total}** pts"
            + (f"  •  🏆 **{score_total:,}** total score" if score_total else "")
        ),
    )
    if avatar_url:
        e.set_thumbnail(url=avatar_url)
    elif brand.logo:
        e.set_thumbnail(url=brand.logo)

    if started:
        lines = [
            f"{r['emoji']} `{r['short']:<5}` {bar(r['done'], r['total'])} "
            f"**{r['done']}/{r['total']}**"
            for r in started
        ]
        e.add_field(name="Progress", value=lines_within(lines), inline=False)
    else:
        e.add_field(name="Progress", value="_Nothing recorded yet._", inline=False)

    if untouched:
        e.add_field(
            name="Not started",
            value=", ".join(r["short"] for r in untouched)[:FIELD_LIMIT],
            inline=False,
        )
    if member_titles:
        e.add_field(name="🏅 Titles",
                    value=" • ".join(member_titles)[:FIELD_LIMIT], inline=False)

    e.set_footer(text=f"{brand.name} • use the menus below to switch role or open a trial")
    return e


def trial_embed(brand: Brand, gamertag: str, role: str, trial: dict, rows: list[dict],
                mark_label: str = "Lead") -> discord.Embed:
    done = sum(1 for r in rows if r["have"])
    marked = sum(1 for r in rows if r.get("mark") == "L")
    e = discord.Embed(
        title=f"{trial['emoji']}  {trial['name']} — {gamertag}",
        color=brand.color,
        description=f"{ROLE_EMOJI[role]} {ROLE_LABEL[role]}  •  "
                    f"{bar(done, len(rows), 10)} **{done}/{len(rows)}**",
    )
    e.add_field(
        name="​",
        value=lines_within([
            f"{MARK if r.get('mark') == 'L' else TICK if r['have'] else CROSS} "
            f"{KIND_ICON.get(r['kind'], '•')} {r['name']}"
            for r in rows
        ]) or "_No achievements configured for this trial._",
        inline=False,
    )
    e.set_footer(text=f"{brand.name}  •  {MARK} = {mark_label}" if marked else brand.name)
    return e


def leaderboard_embed(brand: Brand, title: str, subtitle: str, entries: list[tuple[str, str]],
                      page: int, pages: int, offset: int = 0) -> discord.Embed:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    for i, (label, value) in enumerate(entries, start=offset + 1):
        lines.append(f"{medals.get(i, f'`{i:>2}`')} **{label}** — {value}")
    e = discord.Embed(
        title=f"📊  {title}",
        description=subtitle + "\n\n" + ("\n".join(lines) or "_No data yet._"),
        color=brand.color,
    )
    e.set_footer(text=f"{brand.name} • page {page}/{pages}")
    return e
