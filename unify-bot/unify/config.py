"""Runtime config. Only the token / guild id / db path come from .env;
everything an admin might want to change lives in the settings table and is
edited in Discord with /config."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Roles a member can hold achievements under.
ROLES = ("tank", "healer", "dps")
ALL_ROLES = ROLES + ("account",)
ROLE_EMOJI = {"tank": "🛡️", "healer": "💚", "dps": "⚔️", "account": "🏆"}
ROLE_LABEL = {"tank": "Tank", "healer": "Healer", "dps": "DPS", "account": "Account Wide"}

# Points used by /leaderboard points.
POINTS = {"clear": 1, "boss": 3, "hm": 5, "title": 10}

# Defaults for every /config key. `type` drives validation in the config cog.
SETTING_DEFAULTS: dict[str, tuple[str, object, str]] = {
    # key: (type, default, human description)
    "guild_name":           ("text",    "Unify", "Name shown on embeds and profile cards"),
    "brand_color":          ("color",   "#8B5CF6", "Accent colour for every embed"),
    "logo_url":             ("text",    "", "Guild logo shown as the embed thumbnail"),
    "submissions_channel":  ("channel", 0, "Channel the bot watches for achievement posts"),
    "audit_channel":        ("channel", 0, "Channel that receives the audit trail"),
    "welcome_channel":      ("channel", 0, "Channel for the welcome card when someone joins"),
    "reports_channel":      ("channel", 0, "Channel for scheduled exports and reports"),
    "admin_roles":          ("roles",   [], "Roles allowed to change data"),
    "viewer_roles":         ("roles",   [], "Roles allowed to look people up (empty = everyone)"),
    "submission_mode":      ("choice:auto,review", "review",
                             "auto = record instantly, review = an admin presses Approve"),
    "notify_dm":            ("bool",    True, "DM a member when they earn something"),
    "mark_label":           ("text",    "Legacy",
                             "Name for the 'L' marker: records from before ESO made "
                             "achievements account-wide"),
    "role_sync":            ("bool",    False,
                             "Give members the matching Discord role when they earn an achievement"),
    "report_day":           ("choice:mon,tue,wed,thu,fri,sat,sun,off", "sun",
                             "Weekly report day (off = disabled)"),
    "report_hour":          ("int",     18, "Hour (0-23, UTC) the weekly report is posted"),
}


@dataclass(frozen=True)
class Env:
    token: str
    guild_id: int
    db_path: str


def load_env() -> Env:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "paste-your-token-here":
        raise SystemExit(
            "DISCORD_TOKEN is missing.\n"
            "Copy .env.example to .env and paste your bot token into it."
        )
    return Env(
        token=token,
        guild_id=int(os.getenv("GUILD_ID", "0") or 0),
        db_path=os.getenv("DB_PATH", "data/unify.sqlite3"),
    )
