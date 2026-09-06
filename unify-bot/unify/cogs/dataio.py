"""Drag-and-drop import, and export in whatever shape somebody needs."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from .. import embeds, importer, store, views
from ..checks import admin_only
from ..config import ROLES, ROLE_LABEL

TABLES = {
    "members": "SELECT id, gamertag, discord_id, active, created_at FROM members",
    "achievements": (
        "SELECT m.gamertag, ma.role, ma.achievement_key, a.name, ma.granted_at "
        "FROM member_achievements ma JOIN members m ON m.id = ma.member_id "
        "JOIN achievements a ON a.key = ma.achievement_key ORDER BY m.gamertag, ma.role"),
    "scores": ("SELECT m.gamertag, s.trial_key, s.score, s.recorded_at FROM scores s "
               "JOIN members m ON m.id = s.member_id ORDER BY m.gamertag"),
    "parses": ("SELECT m.gamertag, p.label, p.dps, p.recorded_at FROM parses p "
               "JOIN members m ON m.id = p.member_id ORDER BY m.gamertag"),
    "role_map": "SELECT discord_role_id, kind, value, label FROM role_map",
    "audit_log": "SELECT id, ts, actor_name, action, summary, undone FROM audit_log",
}


class DataIO(commands.Cog):
    """Import & export"""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ export
    async def build_export(self, kind: str) -> discord.File:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if kind == "db":
            # fold the write-ahead log in first, or the copy misses recent changes
            await self.bot.db.run("PRAGMA wal_checkpoint(TRUNCATE)")
            return discord.File(self.bot.db.path, filename=f"unify-{stamp}.sqlite3")

        data: dict[str, tuple[list[str], list[tuple]]] = {}
        for name, sql in TABLES.items():
            rows = await self.bot.db.all(sql)
            headers = list(rows[0].keys()) if rows else []
            data[name] = (headers, [tuple(r) for r in rows])

        if kind == "xlsx":
            from openpyxl import Workbook

            wb = Workbook()
            wb.remove(wb.active)
            for name, (headers, rows) in data.items():
                ws = wb.create_sheet(name[:31])
                ws.append(headers)
                for row in rows:
                    ws.append(list(row))
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            return discord.File(buf, filename=f"unify-{stamp}.xlsx")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, (headers, rows) in data.items():
                text = io.StringIO()
                writer = csv.writer(text)
                writer.writerow(headers)
                writer.writerows(rows)
                zf.writestr(f"{name}.csv", text.getvalue())
        buf.seek(0)
        return discord.File(buf, filename=f"unify-{stamp}-csv.zip")

    @app_commands.command(description="Download the whole database")
    @app_commands.choices(fmt=[
        app_commands.Choice(name="Excel workbook (.xlsx)", value="xlsx"),
        app_commands.Choice(name="CSV files (.zip)", value="csv"),
        app_commands.Choice(name="Raw database (.sqlite3)", value="db"),
    ])
    @admin_only()
    async def export(self, interaction: discord.Interaction,
                     fmt: app_commands.Choice[str] | None = None):
        await interaction.response.defer(thinking=True, ephemeral=True)
        kind = fmt.value if fmt else "xlsx"
        file = await self.build_export(kind)
        await interaction.followup.send(
            embed=embeds.success(f"Export ready — {datetime.now(timezone.utc):%d %b %Y %H:%M} UTC"),
            file=file, ephemeral=True)
        await self.bot.audit(interaction.user, "export", kind)

    # ------------------------------------------------------------------ import
    @app_commands.command(name="import", description="Upload a spreadsheet and I'll read it")
    @app_commands.describe(
        file="Drag your .xlsx or .csv straight onto Discord",
        role="Only needed for a CSV that doesn't say which role it is",
    )
    @app_commands.choices(role=[app_commands.Choice(name=ROLE_LABEL[r], value=r)
                                for r in ROLES] +
                               [app_commands.Choice(name="Account Wide", value="account")])
    @admin_only()
    async def import_cmd(self, interaction: discord.Interaction, file: discord.Attachment,
                         role: app_commands.Choice[str] | None = None):
        await interaction.response.defer(thinking=True)
        try:
            sheets = importer.read_file(await file.read(), file.filename)
        except Exception as exc:
            return await interaction.followup.send(embed=embeds.error(str(exc)))

        keys = {r[0] for r in await self.bot.db.all("SELECT key FROM achievements")}
        trials = {r[0] for r in await self.bot.db.all("SELECT key FROM trials")}
        plans = [
            importer.plan_sheet(name, headers, rows, keys, trials, role.value if role else "")
            for name, (headers, rows) in sheets.items()
        ]
        usable = [p for p in plans if p.kind != "skip"]

        e = embeds.base(self.bot.brand, f"📥  {file.filename}")
        lines = []
        for p in plans:
            if p.kind == "skip":
                lines.append(f"⏭️ **{p.name}** — skipped ({p.reason})")
            elif p.kind == "achievements":
                lines.append(f"✅ **{p.name}** — {p.rows} people, {len(p.value_cols)} "
                             f"achievement columns → **{ROLE_LABEL[p.role]}**")
            else:
                lines.append(f"✅ **{p.name}** — {p.rows} people, "
                             f"{len(p.value_cols)} {p.kind[:-1]} columns")
        e.description = "\n".join(lines)
        ignored = sorted({c for p in usable for c in p.ignored})
        if ignored:
            e.add_field(name="Columns I'll ignore",
                        value=", ".join(ignored[:20]) + ("…" if len(ignored) > 20 else ""),
                        inline=False)
        if not usable:
            e.color = 0xEF4444
            e.add_field(name="Nothing to import",
                        value="I need a **GamerTag** column and columns named after "
                              "achievements. `/export` gives you a file in the right shape.",
                        inline=False)
            return await interaction.followup.send(embed=e)

        e.add_field(
            name="​",
            value="People who aren't on the roster yet will be added, and existing records "
                  "are updated rather than wiped. A bulk import is **not** covered by "
                  "`/undo` — run `/export` first if you want a restore point.",
            inline=False)
        view = views.Confirm(interaction.user.id, "Import", discord.ButtonStyle.success)
        message = await interaction.followup.send(embed=e, view=view, wait=True)
        view.message = message
        await view.wait()
        if not view.result:
            return await message.edit(embed=embeds.warn("Cancelled — nothing was imported."),
                                      view=None)

        await message.edit(embed=embeds.info(self.bot.brand, "Importing…"), view=None)
        report = await self.apply(sheets, usable, interaction.user)
        await self.bot.audit(interaction.user, "import", f"{file.filename} — {report}")
        await message.edit(embed=embeds.success(f"Imported **{file.filename}**\n{report}"))

    async def apply(self, sheets, plans: list[importer.SheetPlan], actor) -> str:
        """Bulk path: the catalog is read once and rows are written straight in,
        so a 500-person workbook doesn't turn into 20,000 audit entries."""
        db = self.bot.db
        catalog = await store.catalog(db)

        def with_prerequisites(keys: list[str]) -> set[str]:
            out, stack = set(), list(keys)
            while stack:
                key = stack.pop()
                if key in out or key not in catalog:
                    continue
                out.add(key)
                stack.extend(catalog[key]["requires"].split())
            return out

        new_people = granted = scored = parsed = 0
        statements: list[tuple[str, tuple]] = []
        for plan in plans:
            _, rows = sheets[plan.name]
            for row in rows:
                gamertag = (str(row[plan.gamertag_col] or "").strip()
                            if plan.gamertag_col < len(row) else "")
                if not gamertag:
                    continue
                discord_id = None
                if 0 <= plan.discord_col < len(row):
                    raw = str(row[plan.discord_col] or "").strip().strip("<@!>")
                    discord_id = int(raw) if raw.isdigit() else None
                member, created = await store.upsert_member(db, gamertag, discord_id)
                new_people += int(created)

                if plan.kind == "achievements":
                    hits = [key for col, key in plan.value_cols.items()
                            if col < len(row) and importer.cell_is_true(row[col])]
                    for key in with_prerequisites(hits):
                        statements.append((
                            "INSERT OR IGNORE INTO member_achievements"
                            "(member_id, role, achievement_key, granted_by) VALUES(?,?,?,?)",
                            (member.id, plan.role, key, actor.id)))
                        granted += 1
                elif plan.kind == "scores":
                    for col, trial_key in plan.value_cols.items():
                        value = importer.cell_number(row[col]) if col < len(row) else None
                        if value:
                            statements.append((
                                "INSERT INTO scores(member_id, trial_key, score, recorded_by) "
                                "VALUES(?,?,?,?) ON CONFLICT(member_id, trial_key) DO UPDATE SET "
                                "score=excluded.score, recorded_at=datetime('now')",
                                (member.id, trial_key, value, actor.id)))
                            scored += 1
                else:
                    for col, label in plan.value_cols.items():
                        value = importer.cell_number(row[col]) if col < len(row) else None
                        if value:
                            statements.append((
                                "INSERT INTO parses(member_id, label, dps, recorded_by) "
                                "VALUES(?,?,?,?) ON CONFLICT(member_id, label) DO UPDATE SET "
                                "dps=excluded.dps, recorded_at=datetime('now')",
                                (member.id, label, value, actor.id)))
                            parsed += 1

        await db.run_many(statements)
        bits = [f"👥 {new_people} new members", f"🏅 {granted} achievements"]
        if scored:
            bits.append(f"🏆 {scored} scores")
        if parsed:
            bits.append(f"⚔️ {parsed} parses")
        summary = " • ".join(bits)
        await store.log_action(db, actor.id, str(actor), "import", summary)
        return summary


async def setup(bot):
    await bot.add_cog(DataIO(bot))
