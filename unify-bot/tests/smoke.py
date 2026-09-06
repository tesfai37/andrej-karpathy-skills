"""Offline checks: build the whole command tree, then exercise the parts that
don't need Discord (message parsing, prerequisite chains, spreadsheet import).

    python tests/smoke.py
"""
from __future__ import annotations

import asyncio
import io
import discord  # noqa: E402
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unify import embeds, importer, parsing, store  # noqa: E402
from unify.bot import COGS, UnifyBot  # noqa: E402
from unify.config import Env  # noqa: E402
from unify.db import Database  # noqa: E402

CATALOG = json.loads(
    (Path(__file__).resolve().parents[1] / "unify" / "data" / "catalog.json").read_text()
)
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


async def fresh_db(path: str) -> Database:
    db = Database(path)
    await db.connect()
    await db.seed_catalog(CATALOG)
    return db


async def test_command_tree() -> None:
    print("\ncommand tree")
    with tempfile.TemporaryDirectory() as tmp:
        bot = UnifyBot(Env(token="x", guild_id=0, db_path=f"{tmp}/t.sqlite3"))
        await bot.db.connect()
        await bot.db.seed_catalog(CATALOG)
        for cog in COGS:
            await bot.load_extension(cog)
        names = sorted(c.name for c in bot.tree.get_commands())
        subs = {
            f"/{c.name} {s.name}"
            for c in bot.tree.get_commands()
            if hasattr(c, "commands") for s in c.commands
        }
        check(f"{len(names)} top-level commands registered", len(names) >= 12, ", ".join(names))
        for expected in ("/map auto", "/member add", "/achievement give", "/record score",
                         "/config show", "/trial add"):
            check(f"{expected} exists", expected in subs)
        for cog in reversed(COGS):        # stops the reports loop the cog started
            await bot.unload_extension(cog)
        await bot.db.close()


async def test_prerequisites() -> None:
    print("\nprerequisite chains")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        got = set(await store.expand_prerequisites(db, ["vsshm"]))
        check("vsshm pulls in every Sunspire boss",
              got == {"vss", "vssice", "vssfire", "vssnavi", "vsshm"}, str(sorted(got)))

        member = await store.create_member(db, "BUDMAN008", 478079177224880128)
        added = await store.grant(db, member, "dps", ["vsshm"], 1, "tester")
        check("granting vsshm records 5 rows", len(added) == 5, str(added))
        again = await store.grant(db, member, "dps", ["vss"], 1, "tester")
        check("re-granting adds nothing", again == [])

        removed = await store.revoke(db, member, "dps", ["vss"], 1, "tester")
        check("revoking vss cascades to everything above it", len(removed) == 5, str(removed))
        check("nothing left on the record", await store.owned(db, member.id, "dps") == set())

        await store.grant(db, member, "dps", ["vssgodslayer"], 1, "tester")
        check("godslayer is worth 6 achievements",
              len(await store.owned(db, member.id, "dps")) == 6)
        check("points are weighted by kind", await store.points(db, member.id) == 1 + 3 * 3 + 5 + 10,
              str(await store.points(db, member.id)))
        check("title shows up", await store.titles(db, member.id) == ["Godslayer"])

        rows = await store.progress(db, member.id, "dps")
        sunspire = next(r for r in rows if r["key"] == "vss")
        check("progress reports 6/6 for Sunspire",
              (sunspire["done"], sunspire["total"]) == (6, 6))

        entry = await db.val("SELECT MAX(id) FROM audit_log")
        check("undo reverses the last grant", await store.undo_action(db, entry) is not None)
        check("record is back to 0 after undo",
              await store.owned(db, member.id, "dps") == set())
        await db.close()


async def test_message_parsing() -> None:
    print("\nreading the submissions channel")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        # the mapping the guild would build with /map auto
        await db.run_many([
            ("INSERT INTO role_map(discord_role_id, kind, value, label) VALUES(?,?,?,?)", row)
            for row in [
                (706551977760260136, "role", "dps", "DPS"),
                (706551972446339103, "achievement", "vas", "vAS"),
                (706551973155045437, "achievement", "vas1", "vAS +1"),
                (706551974245695548, "achievement", "vas2", "vAS +2"),
                (706551975033962586, "achievement", "vasir", "Immortal Redeemer"),
            ]
        ])

        parsed = await parsing.parse_message(
            db, "<@478079177224880128> dps <@&706551977760260136>")
        check("user mention picked up", parsed.discord_ids == [478079177224880128])
        check("role mention resolves to dps", parsed.roles == ["dps"])

        parsed = await parsing.parse_message(
            db, "<@478079177224880128> <@&706551972446339103> <@&706551973155045437> "
                "<@&706551974245695548> <@&706551975033962586>")
        check("four achievement roles resolve",
              parsed.keys == ["vas", "vas1", "vas2", "vasir"], str(parsed.keys))
        check("nothing left unmapped", parsed.unmapped_role_ids == [])

        parsed = await parsing.parse_message(db, "BUDMAN008 healer vsshm vkahm")
        check("plain typed keys work too",
              parsed.keys == ["vsshm", "vkahm"] and parsed.roles == ["healer"])
        check("gamertag survives as a leftover word",
              "BUDMAN008" in parsed.leftover_words)

        parsed = await parsing.parse_message(db, "<@1> dps <@&999999999999999999>")
        check("an unknown role is reported, not dropped",
              parsed.unmapped_role_ids == [999999999999999999])
        await db.close()


def make_workbook() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    tank = wb.active
    tank.title = "Tank"
    tank.append(["GamerTag", "DiscordName", "vas", "vas1", "vas2", "vasir", "bogus"])
    tank.append(["BUDMAN008", "budman008", "X", "X", "", "", "junk"])
    tank.append(["ALICE", "alice", "X", "", "", "", ""])

    score = wb.create_sheet("Score")
    score.append(["GamerTag", "vas", "vss"])
    score.append(["BUDMAN008", "112k", "98,400"])

    parse = wb.create_sheet("Parse")
    parse.append(["GamerTag", "3m dummy"])
    parse.append(["BUDMAN008", "112500"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_import() -> None:
    print("\nspreadsheet import")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        sheets = importer.read_file(make_workbook(), "roster.xlsx")
        keys = {r[0] for r in await db.all("SELECT key FROM achievements")}
        trials = {r[0] for r in await db.all("SELECT key FROM trials")}
        plans = {n: importer.plan_sheet(n, h, r, keys, trials)
                 for n, (h, r) in sheets.items()}

        check("Tank sheet maps to the tank role", plans["Tank"].role == "tank")
        check("four achievement columns found", len(plans["Tank"].value_cols) == 4)
        check("unknown column ignored, not fatal", plans["Tank"].ignored == ["bogus"])
        check("Score sheet detected", plans["Score"].kind == "scores")
        check("Parse sheet detected", plans["Parse"].kind == "parses")

        check("112k parses to 112000", importer.cell_number("112k") == 112000)
        check("112k dps keeps its thousands", importer.cell_number("112k dps") == 112000)
        check("prose is not mined for digits",
              importer.cell_number("see notes 2024") is None
              and importer.cell_number("did 3 runs") is None)
        check("n/a and dashes are not zero",
              importer.cell_number("n/a") is None and importer.cell_number("-") is None)
        check("98,400 parses to 98400", importer.cell_number("98,400") == 98400)
        check("1.2m parses to 1200000", importer.cell_number("1.2m") == 1200000)
        check("blank cell is None", importer.cell_number("") is None)
        check("X counts as earned", importer.cell_is_true("X") and importer.cell_is_true("✅"))
        check("dash does not", not importer.cell_is_true("-"))
        await db.close()



async def test_identity_safety() -> None:
    """Regressions that used to hand one member another member's record."""
    print("\nidentity safety")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        alice = await store.create_member(db, "ALICE", None)          # row id 1
        bob = await store.create_member(db, "BOB", None)              # row id 2
        await store.create_member(db, "1234", None)                   # numeric gamertag

        check("a bare number is not a database row id",
              await store.find_member(db, str(bob.id)) is None,
              f"looking up '{bob.id}' must not return BOB")
        check("a numeric gamertag still resolves to itself",
              (await store.find_member(db, "1234")).gamertag == "1234")
        check("a real snowflake still resolves",
              (await store.find_member(db, "<@478079177224880128>")) is None)
        await db.run("UPDATE members SET discord_id = 478079177224880128 WHERE id = ?",
                     (alice.id,))
        check("…once somebody owns it",
              (await store.find_member(db, "<@478079177224880128>")).gamertag == "ALICE")

        # the silent-merge regression
        carol = (await store.upsert_member(db, "CAROL", 111)).member
        await store.grant(db, carol, "dps", ["vsshm"], 1, "tester")
        result = await store.upsert_member(db, "DAVE", 111)
        check("a new gamertag never renames the account's current owner",
              result.member.gamertag == "DAVE" and result.created)
        check("…and the original member is untouched",
              (await store.get_member(db, carol.id)).gamertag == "CAROL")
        check("…keeping all five achievements",
              len(await store.owned(db, carol.id, "dps")) == 5)
        check("…and the clash is reported, not swallowed", bool(result.conflict),
              result.conflict)
        check("DAVE was left unlinked rather than stealing the account",
              result.member.discord_id is None)

        linked = await store.upsert_member(db, "BOB", 222)
        check("an unclaimed gamertag still gets linked normally",
              linked.member.discord_id == 222 and not linked.conflict)
        await db.close()


async def test_display_limits() -> None:
    """Everything an admin can type must survive Discord's validation."""
    print("\ndisplay safety")
    check("a progress bar can never overrun its width", len(embeds.bar(9, 5, 6)) == 6)
    check("an empty bar is still full width", len(embeds.bar(0, 5, 6)) == 6)
    check("a word is rejected as an emoji", embeds.as_emoji("bone") is None)
    check("a real emoji is kept", embeds.as_emoji("🐉") == "🐉")
    check("a custom server emoji is kept",
          embeds.as_emoji("<:vss:706551984019742740>") is not None)
    check("a blank emoji is None", embeds.as_emoji("  ") is None)
    check("SelectOption accepts what as_emoji allows",
          discord.SelectOption(label="x", value="x",
                               emoji=embeds.as_emoji("bone")).emoji is None)

    brand = embeds.Brand()
    rows = [{"key": f"t{i}", "name": f"Trial number {i}", "short": f"vT{i:02d}",
             "emoji": "🐉", "sort": i, "total": 6, "done": 3} for i in range(60)]
    e = embeds.profile_embed(brand, "X" * 40, 1, "dps", rows,
                             [f"Title {i}" for i in range(60)], 999_999, 4242)
    check("a 60-trial profile stays inside every embed limit",
          all(len(f.value) <= 1024 for f in e.fields) and len(e) <= 6000,
          f"{len(e)} chars, longest field {max(len(f.value) for f in e.fields)}")

    detail = [{"key": f"a{i}", "name": f"A very long achievement name number {i}",
               "kind": "boss", "have": i % 2, "granted_at": None} for i in range(60)]
    e = embeds.trial_embed(brand, "X", "dps", {"emoji": "🐉", "name": "Trial"}, detail)
    check("a 60-achievement trial page stays inside its limits",
          all(len(f.value) <= 1024 for f in e.fields) and len(e) <= 6000)

async def main() -> None:
    await test_command_tree()
    await test_prerequisites()
    await test_message_parsing()
    await test_import()
    await test_identity_safety()
    await test_display_limits()
    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
