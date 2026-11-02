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

from unify import embeds, importer, parsing, reviews, rolesync, store  # noqa: E402
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
        check("/find is registered", "find" in names)
        check("/status is registered", "status" in names)
        from unify.cogs.helpmenu import SECTIONS
        listed = {cmd for _, _, _, rows in SECTIONS for cmd, _ in rows}
        real = {f"/{n}" for n in names} | subs
        check("every command in /help exists", listed <= real, str(listed - real))
        hidden = {f"/{c.name}" for c in bot.tree.get_commands()
                  if not hasattr(c, "commands")} | subs
        check("every command exists in /help", hidden <= listed | {"/help"},
              str(hidden - listed - {"/help"}))
        for gone in ("score", "parse", "whoami"):
            check(f"/{gone} has been folded away", gone not in names)
        check("/guide list has been folded away", "/guide list" not in subs)
        for expected in ("/map auto", "/member add", "/achievement give", "/record score",
                         "/config show", "/trial add",
                         "/sync all", "/sync member", "/sync check"):
            check(f"{expected} exists", expected in subs)
        for cog in reversed(COGS):        # stops the reports loop the cog started
            await bot.unload_extension(cog)
        await bot.db.close()


async def test_prerequisites() -> None:
    print("\nprerequisite chains")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        got = set(await store.expand_prerequisites(db, ["extinguisher"]))
        check("extinguisher pulls in every Sunspire boss",
              got == {"vss", "vssice", "vssfire", "vssnavi", "extinguisher"}, str(sorted(got)))

        member = await store.create_member(db, "BUDMAN008", 478079177224880128)
        added = await store.grant(db, member, "dps", ["extinguisher"], 1, "tester")
        check("granting extinguisher records 5 rows", len(added) == 5, str(added))
        again = await store.grant(db, member, "dps", ["vss"], 1, "tester")
        check("re-granting adds nothing", again == [])

        removed = await store.revoke(db, member, "dps", ["vss"], 1, "tester")
        check("revoking vss cascades to everything above it", len(removed) == 5, str(removed))
        check("nothing left on the record", await store.owned(db, member.id, "dps") == set())

        await store.grant(db, member, "dps", ["godslayer"], 1, "tester")
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

        parsed = await parsing.parse_message(db, "BUDMAN008 healer extinguisher vkahm")
        check("plain typed keys work too",
              parsed.keys == ["extinguisher", "vkahm"] and parsed.roles == ["healer"])
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
        await store.grant(db, carol, "dps", ["extinguisher"], 1, "tester")
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

    one_long = ", ".join(f"`topic-{i:03d}`" for i in range(90))
    packed = embeds.lines_within([one_long])
    check("one over-long line is trimmed, not thrown away",
          "topic-000" in packed and len(packed) <= 1024, repr(packed[:40]))


# --- just enough of discord.Guild to exercise the role-sync arithmetic -------
class FakeRole:
    def __init__(self, rid, name, position, managed=False):
        self.id, self.name, self.position, self.managed = rid, name, position, managed
        self.mention = f"<@&{rid}>"

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return self.position == 0

    def __repr__(self):
        return self.name


class FakePerms:
    def __init__(self, manage_roles=True):
        self.manage_roles = manage_roles


class FakePerson:
    def __init__(self, roles, manage_roles=True):
        self.roles = roles
        self.guild_permissions = FakePerms(manage_roles)

    @property
    def top_role(self):
        return max(self.roles, key=lambda r: r.position)


class FakeGuild:
    def __init__(self, roles, people, me):
        self._roles = {r.id: r for r in roles}
        self._people = people
        self.me = me

    def get_role(self, rid):
        return self._roles.get(rid)

    def get_member(self, uid):
        return self._people.get(uid)


async def test_role_sync() -> None:
    print("\nrole sync")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        vss = FakeRole(10, "vSS", 5)
        extinguisher = FakeRole(11, "vSS HM", 6)
        godslayer = FakeRole(12, "Godslayer", 7)
        too_high = FakeRole(13, "vKA HM", 99)
        unrelated = FakeRole(14, "Raider", 4)
        bot_role = FakeRole(99, "Unify Bot", 50)

        await db.run_many([
            ("INSERT INTO role_map(discord_role_id, kind, value, label) VALUES(?,?,?,?)", row)
            for row in [(10, "achievement", "vss", "vSS"),
                        (11, "achievement", "extinguisher", "vSS HM"),
                        (12, "achievement", "godslayer", "Godslayer"),
                        (13, "achievement", "vkahm", "vKA HM")]
        ])

        member = await store.create_member(db, "BUDMAN008", 4001)
        # they hold an unrelated role plus a stale Godslayer they never earned
        person = FakePerson([unrelated, godslayer])
        me = FakePerson([bot_role])
        guild = FakeGuild([vss, extinguisher, godslayer, too_high, unrelated, bot_role],
                          {4001: person}, me)

        await store.grant(db, member, "dps", ["extinguisher"], 1, "tester")   # -> vss + extinguisher
        plan = await rolesync.plan_member(db, guild, member)
        check("earned roles are queued to add",
              {r.name for r in plan.add} == {"vSS", "vSS HM"}, str(plan.add))
        check("a role they no longer qualify for is queued to remove",
              [r.name for r in plan.remove] == ["Godslayer"], str(plan.remove))
        check("a role the bot never mapped is left alone",
              unrelated not in plan.add and unrelated not in plan.remove)

        await store.grant(db, member, "dps", ["vkahm"], 1, "tester")
        plan = await rolesync.plan_member(db, guild, member)
        check("a role above the bot is reported, not attempted",
              [r.name for r in plan.blocked] == ["vKA HM"] and too_high not in plan.add)

        no_perm = FakeGuild([vss, bot_role], {4001: FakePerson([unrelated])},
                            FakePerson([bot_role], manage_roles=False))
        plan = await rolesync.plan_member(db, no_perm, member)
        check("without Manage Roles nothing is attempted", plan.add == [] and plan.blocked)

        unlinked = await store.create_member(db, "NOTLINKED", None)
        check("an unlinked member is skipped with a reason",
              (await rolesync.plan_member(db, guild, unlinked)).skipped == "no Discord account linked")
        gone = await store.create_member(db, "LEFT", 9999)
        check("somebody who left the server is skipped",
              (await rolesync.plan_member(db, guild, gone)).skipped == "not in the server")
        await db.close()


async def test_find() -> None:
    print("\nfind")
    from unify.cogs.find import Find

    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        cog = Find(type("Shim", (), {"db": db})())

        ace = await store.create_member(db, "ACE", 1)
        rook = await store.create_member(db, "ROOK", 2)
        healer = await store.create_member(db, "MENDER", 3)
        await store.create_member(db, "BENCHED", 4)

        await store.grant(db, ace, "dps", ["godslayer"], 1, "t")     # everything vSS
        await store.grant(db, rook, "dps", ["extinguisher"], 1, "t")           # vSS HM, no title
        await store.grant(db, healer, "healer", ["extinguisher"], 1, "t")      # as a healer
        await store.set_parse(db, ace, "3m dummy", 118_000, 1, "t")
        await store.set_parse(db, rook, "3m dummy", 92_000, 1, "t")

        async def names(**kw):
            kw.setdefault("has", []); kw.setdefault("missing", [])
            kw.setdefault("role", None); kw.setdefault("min_parse", None)
            kw.setdefault("parse_label", None)
            return [r["gamertag"] for r in await cog.search(**kw)]

        check("has: finds everyone with the clear",
              sorted(await names(has=["extinguisher"])) == ["ACE", "MENDER", "ROOK"])
        check("role: narrows to how it was cleared",
              sorted(await names(has=["extinguisher"], role="dps")) == ["ACE", "ROOK"])
        check("missing: the actual raid-building question",
              await names(has=["extinguisher"], missing=["godslayer"], role="dps") == ["ROOK"])
        check("min_parse filters on numbers",
              await names(has=["extinguisher"], min_parse=100_000) == ["ACE"])
        check("parse_label scopes the number",
              await names(min_parse=90_000, parse_label="nonexistent") == [])
        check("results lead with the most experienced",
              await names(has=["vss"], role="dps") == ["ACE", "ROOK"])
        check("nobody on the bench sneaks in", "BENCHED" not in await names(has=["vss"]))

        good, bad = await cog.validate("extinguisher nonsense vkahm")
        check("unknown codes are rejected, known ones kept",
              good == ["extinguisher", "vkahm"] and bad == ["nonsense"])
        await db.close()


async def test_marks_and_formats() -> None:
    """The guild's sheet uses a second marker and its own number style."""
    print("\nreal-world data quirks")
    from unify.config import SETTING_DEFAULTS
    check("X is a clear", importer.cell_mark("X") == "X")
    check("L is kept as its own marker", importer.cell_mark("l") == "L")
    check("the marker is named Legacy out of the box",
          SETTING_DEFAULTS["mark_label"][1] == "Legacy")
    check("both markers count as 'they have it'",
          importer.cell_is_true("X") and importer.cell_is_true("L"))
    check("notes typed into a cell are neither",
          importer.cell_mark("banned") is None and importer.cell_mark("NA/EU") is None)
    check("…and are distinguishable from a blank cell",
          not importer.cell_is_blank("banned") and importer.cell_is_blank("-"))

    check("112k means 112,000", importer.cell_number("112k") == 112_000)
    check("245,469k means 245,469 - the k is noise on a full number",
          importer.cell_number("245,469k") == 245_469)
    check("620,580k likewise", importer.cell_number("620,580k") == 620_580)
    check("1.2m still multiplies", importer.cell_number("1.2m") == 1_200_000)

    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        member = await store.create_member(db, "OLDTIMER", 1)
        await store.grant(db, member, "dps", ["vmol"], 1, "t", mark="L")
        rows = await store.trial_detail(db, member.id, "dps", "vmol")
        check("the marker survives into the trial view",
              [r["mark"] for r in rows if r["have"]] == ["L"])
        check("a marked achievement still counts as held",
              "vmol" in await store.owned(db, member.id, "dps"))
        e = embeds.trial_embed(embeds.Brand(), "OLDTIMER", "dps",
                               {"emoji": "🌑", "name": "Maw"}, rows, "Legacy")
        check("the trial page shows the marker and explains it",
              embeds.MARK in e.fields[0].value and "Legacy" in e.footer.text)
        await db.close()


async def test_name_matching() -> None:
    print("\nlinking old Discord names")
    from unify.cogs.admin import strip_discriminator
    check("a legacy discriminator is ignored",
          strip_discriminator("A Sly Man#9733") == "a sly man")
    check("a modern username is untouched",
          strip_discriminator("crow.man") == "crow.man")
    check("trailing dots are kept - they are part of the name",
          strip_discriminator("adecentdude.") == "adecentdude.")
    check("a four-digit name is not mistaken for a discriminator",
          strip_discriminator("1234") == "1234")


async def test_guild_only() -> None:
    """Commands read guild state, so a DM must never reach them."""
    print("\ncommands are guild-only")
    with tempfile.TemporaryDirectory() as tmp:
        bot = UnifyBot(Env(token="x", guild_id=0, db_path=f"{tmp}/t.sqlite3"))
        await bot.db.connect()
        await bot.db.seed_catalog(CATALOG)
        for cog in COGS:
            await bot.load_extension(cog)
        bot.restrict_to_guilds()
        top = bot.tree.get_commands()
        check("every command refuses DMs",
              all(c.allowed_contexts and c.allowed_contexts.guild
                  and not c.allowed_contexts.dm_channel
                  and not c.allowed_contexts.private_channel for c in top),
              f"{len(top)} commands")
        # the payload builder is fussy about which AppCommandContext class it gets
        contexts = {tuple(c.to_dict(bot.tree).get("contexts") or ()) for c in top}
        check("the sync payload builds and says guild-only", contexts == {(0,)}, str(contexts))
        for cog in reversed(COGS):
            await bot.unload_extension(cog)
        await bot.db.close()


async def test_export_round_trip() -> None:
    """A backup has to restore. Export the database, read it back into an empty
    one, and compare - this is what caught the reference sheets colliding with
    the importer's own sheet names."""
    print("\nexport survives a round trip")
    from unify.cogs.dataio import DataIO

    class Shim:
        def __init__(self, db):
            self.db, self.brand = db, embeds.Brand()

        async def setting(self, key):
            return {"role_sync": False, "mark_label": "Legacy"}[key]

    class Actor:
        id = 1

        def __str__(self):
            return "tester"

    with tempfile.TemporaryDirectory() as tmp:
        src = await fresh_db(f"{tmp}/src.sqlite3")
        ace = await store.create_member(src, "ACE", 700000000000000001)
        rook = await store.create_member(src, "ROOK", None)
        await store.grant(src, ace, "dps", ["godslayer"], 1, "t")
        await store.grant(src, ace, "tank", ["vmol"], 1, "t", mark="L")
        await store.grant(src, rook, "healer", ["extinguisher"], 1, "t")
        await store.set_score(src, ace, "vss", 257_498, 1, "t")
        await store.set_parse(src, ace, "Arcanist", 118_000, 1, "t")
        await src.run("INSERT INTO guides(category, topic, body) VALUES('cp','tank','64 Blessed')")

        before = await census(src)
        workbook = (await DataIO(Shim(src)).build_export("xlsx")).fp.read()
        await src.close()

        dst = await fresh_db(f"{tmp}/dst.sqlite3")
        sheets = importer.read_file(workbook, "unify.xlsx")
        keys = {r[0] for r in await dst.all("SELECT key FROM achievements")}
        trials = {r[0] for r in await dst.all("SELECT key FROM trials")}
        plans = [importer.plan_sheet(n, h, r, keys, trials) for n, (h, r) in sheets.items()]
        await DataIO(Shim(dst)).apply(sheets, [p for p in plans if p.kind != "skip"], Actor())
        after = await census(dst)

        check("members come back", after["members"] == before["members"])
        check("normal clears come back", after["ach_X"] == before["ach_X"],
              f"{before['ach_X']} -> {after['ach_X']}")
        check("legacy marks survive the round trip", after["ach_L"] == before["ach_L"],
              f"{before['ach_L']} -> {after['ach_L']}")
        check("the Discord link survives", after["linked"] == before["linked"])
        check("scores come back exactly", after["score_sum"] == before["score_sum"])
        check("parses are not duplicated by the reference sheets",
              after["parses"] == before["parses"], f"{before['parses']} -> {after['parses']}")
        check("guide entries come back", after["guides"] == before["guides"],
              f"{before['guides']} -> {after['guides']}")
        await dst.close()


async def census(db) -> dict[str, int]:
    return {
        "members": await db.val("SELECT COUNT(*) FROM members", (), 0),
        "linked": await db.val(
            "SELECT COUNT(*) FROM members WHERE discord_id IS NOT NULL", (), 0),
        "ach_X": await db.val(
            "SELECT COUNT(*) FROM member_achievements WHERE mark='X'", (), 0),
        "ach_L": await db.val(
            "SELECT COUNT(*) FROM member_achievements WHERE mark='L'", (), 0),
        "score_sum": await db.val("SELECT COALESCE(SUM(score),0) FROM scores", (), 0),
        "parses": await db.val("SELECT COUNT(*) FROM parses", (), 0),
        "guides": await db.val("SELECT COUNT(*) FROM guides", (), 0),
    }


async def test_trust_routing() -> None:
    """Who is allowed to cause a write. This is the rule the rest of the design
    rests on, so every branch of it is pinned here."""
    print("\ntrust routing")

    def decide(**kw):
        base = dict(submitter_id=1, subject_discord_id=2, submitter_trusted=True,
                    member_known=True, all_roles_mapped=True, removes=False)
        base.update(kw)
        return reviews.route(**base)

    check("a trusted recorder applies straight away", decide().auto)
    check("an untrusted member never applies", not decide(submitter_trusted=False).auto)
    check("a recorder still cannot record for themselves",
          not decide(subject_discord_id=1).auto,
          decide(subject_discord_id=1).note)
    check("an unknown member always queues", not decide(member_known=False).auto)
    check("an unrecognised role always queues", not decide(all_roles_mapped=False).auto)
    check("a removal is never automatic", not decide(removes=True).auto)
    check("self-submission outranks trust",
          not decide(subject_discord_id=1, submitter_trusted=True).auto)
    check("every refusal explains itself",
          all(decide(**kw).note for kw in (
              {"submitter_trusted": False}, {"subject_discord_id": 1},
              {"member_known": False}, {"all_roles_mapped": False}, {"removes": True})))
    check("an approval carries no complaint", decide().note == "")


async def test_review_queue() -> None:
    print("\nreview queue")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        ace = await store.create_member(db, "ACE", 4001)

        rid = await reviews.open_request(
            db, "achievements", requester_id=9, member_id=ace.id, role="dps",
            payload={"keys": ["extinguisher"]}, note="not a recorder")
        check("a request starts pending, writing nothing",
              await reviews.pending_count(db) == 1
              and await store.owned(db, ace.id, "dps") == set())

        dup = await reviews.duplicate_request(
            db, "achievements", ace.id, "dps", {"keys": ["extinguisher"]})
        check("an identical waiting request is spotted", dup == rid)
        check("a different one is not",
              await reviews.duplicate_request(
                  db, "achievements", ace.id, "dps", {"keys": ["vkahm"]}) is None)

        result = await reviews.apply(db, await reviews.get(db, rid), 7, "officer")
        await reviews.close(db, rid, "approved", 7)
        check("approving writes the record", result.ok
              and len(await store.owned(db, ace.id, "dps")) == 5)
        check("the queue empties", await reviews.pending_count(db) == 0)
        check("approving twice is impossible",
              await reviews.by_message(db, 0) is None)

        check("a clear already held is recognised",
              await reviews.already_recorded(db, ace.id, "dps", ["vss"]))
        check("one they lack is not",
              not await reviews.already_recorded(db, ace.id, "dps", ["vkahm"]))
        await db.close()


async def test_roster_claims() -> None:
    """A claim on an existing gamertag is an identity takeover attempt."""
    print("\nroster claims")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        veteran = await store.create_member(db, "VETERAN", None)
        await store.grant(db, veteran, "dps", ["godslayer"], 1, "t")

        got, scores = await reviews.inherits(db, "VETERAN")
        check("officers are shown what a claim would hand over", got == 6, f"{got} achievements")

        rid = await reviews.open_request(db, "register", requester_id=5001,
                                         subject="VETERAN")
        check("raising a claim links nothing on its own",
              (await store.get_member(db, veteran.id)).discord_id is None)

        result = await reviews.apply(db, await reviews.get(db, rid), 7, "officer")
        check("approving links it", result.ok
              and (await store.get_member(db, veteran.id)).discord_id == 5001)

        rid2 = await reviews.open_request(db, "register", requester_id=6002,
                                          subject="VETERAN")
        second = await reviews.apply(db, await reviews.get(db, rid2), 7, "officer")
        check("a second account cannot take the same gamertag",
              not second.ok, second.summary)

        rid3 = await reviews.open_request(db, "register", requester_id=5001,
                                          subject="SOMEONEELSE")
        third = await reviews.apply(db, await reviews.get(db, rid3), 7, "officer")
        check("one Discord account cannot hold two gamertags",
              not third.ok, third.summary)
        await db.close()


async def test_submission_modes() -> None:
    """The complete matrix of who causes a write, across both modes."""
    print("\nsubmission modes")

    def d(mode, **kw):
        base = dict(is_recorder=False, has_role=True, submitter_id=1,
                    subject_discord_id=2, member_known=True, all_roles_mapped=True)
        base.update(kw)
        return reviews.decide(mode, **base)

    check("review mode queues even a recorder", not d("review", is_recorder=True).auto)
    check("trusted mode queues an ordinary member", not d("trusted").auto)
    check("trusted mode applies for a recorder", d("trusted", is_recorder=True).auto)
    check("there is no mode where an ordinary member's post applies",
          not any(d(mode).auto for mode in ("review", "trusted", "auto")))

    for mode in ("review", "trusted"):
        check(f"{mode}: nobody records their own clear",
              not d(mode, is_recorder=True, subject_discord_id=1).auto)
        check(f"{mode}: an unknown member always queues",
              not d(mode, is_recorder=True, member_known=False).auto)
        check(f"{mode}: an unmapped role always queues",
              not d(mode, is_recorder=True, all_roles_mapped=False).auto)
        check(f"{mode}: a post with no role always queues",
              not d(mode, is_recorder=True, has_role=False).auto)

    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        await db.set_setting("submission_mode", "auto")
        await db.close()
        db = Database(f"{tmp}/t.sqlite3")
        await db.connect()
        check("a server left on the old auto mode starts up on trusted",
              await db.get_setting("submission_mode") == "trusted")
        await db.close()


async def test_member_input_is_inert() -> None:
    """Whatever a member sends, nothing changes until an officer approves."""
    print("\nmember input writes nothing")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        member = await store.create_member(db, "CHANCER", 900)

        before = await census(db)
        await reviews.open_request(db, "score", requester_id=900, member_id=member.id,
                                   payload={"trial": "vss", "score": 999999},
                                   proof_url="http://example/x.png")
        await reviews.open_request(db, "parse", requester_id=900, member_id=member.id,
                                   payload={"label": "Arcanist", "dps": 999999})
        await reviews.open_request(db, "register", requester_id=901, subject="CHANCER")
        after = await census(db)
        check("three requests changed no guild data", before == after,
              f"{await reviews.pending_count(db)} waiting")

        rows = await reviews.pending(db)
        result = await reviews.apply(db, rows[0], 7, "officer")
        check("only approval writes", result.ok
              and await db.val("SELECT score FROM scores WHERE member_id=?",
                               (member.id,), 0) == 999999)

        claim = [r for r in rows if r["kind"] == "register"][0]
        taken = await reviews.apply(db, claim, 7, "officer")
        check("a claim on a linked gamertag is refused at approval time",
              not taken.ok, taken.summary)
        await db.close()


async def test_leaderboard_top_n() -> None:
    print("\nleaderboard top-N")
    from unify.cogs.leaderboards import Leaderboards

    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        for i in range(40):
            m = await store.create_member(db, f"P{i:02d}", None)
            await store.grant(db, m, "dps", ["vss"], 1, "t")
        cog = Leaderboards(type("S", (), {"db": db, "brand": embeds.Brand()})())
        entries, _ = await cog.fetch("points", None, None)
        check("the board still knows about everyone", len(entries) == 40)
        check("but a caller can ask for just the top", len(entries[:25]) == 25)
        await db.close()


def test_gamertag_validation() -> None:
    print("\ngamertag validation")
    from unify.cogs.membership import GAMERTAG
    for good in ("BUDMAN008.", "A Decent Dude.", "x_y-z", "o'brien", "ACME NoLiFe."):
        check(f"accepts {good!r}", bool(GAMERTAG.match(good)))
    for bad in ("", "x", "<@1234>", "a" * 41, "drop;table", "hi\nthere"):
        check(f"rejects {bad[:14]!r}", not GAMERTAG.match(bad))


async def test_name_lookup() -> None:
    """Officers identify people by Discord name, as the old !add_achievement did."""
    print("\nfinding people by the name you know them by")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        crow = await store.create_member(db, "A Crow Man.", None)
        await db.run("UPDATE members SET legacy_name = 'crow.man' WHERE id = ?", (crow.id,))
        sly = await store.create_member(db, "A Sly Man.", None)
        await db.run("UPDATE members SET legacy_name = 'A Sly Man#9733' WHERE id = ?", (sly.id,))
        for tag in ("I rekindle I.", "Sarraski X."):
            m = await store.create_member(db, tag, None)
            await db.run("UPDATE members SET legacy_name = 'kndl' WHERE id = ?", (m.id,))
        await store.create_member(db, "S superballs B", None)
        await store.create_member(db, "S superballs B.", None)

        async def who(text):
            got = await store.lookup_member(db, text)
            return got.member.gamertag if got.member else [m.gamertag for m in got.candidates]

        check("an old Discord name finds the gamertag", await who("crow.man") == "A Crow Man.")
        check("the old #1234 suffix is optional", await who("a sly man") == "A Sly Man.")
        check("…and accepted when given", await who("A Sly Man#9733") == "A Sly Man.")
        check("the trailing dot is optional", await who("a crow man") == "A Crow Man.")
        check("a name two people share is refused, not guessed",
              await who("kndl") == ["I rekindle I.", "Sarraski X."], str(await who("kndl")))
        check("two records differing only by a dot are both offered, not guessed",
              await who("s superballs b..") == ["S superballs B", "S superballs B."],
              str(await who("s superballs b..")))
        check("an exact gamertag always wins", await who("S superballs B.") == "S superballs B.")
        check("identity checks stay exact - no Discord-name fallback",
              await store.find_member(db, "crow.man") is None)
        check("nobody is invented for an unknown name", await who("nobody-at-all") == [])
        await db.close()


async def test_run_format() -> None:
    """One post can record a whole run, and the guild's existing one-person
    format keeps working exactly as before."""
    print("\nreading a whole run from one post")
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        await db.run_many([
            ("INSERT INTO role_map VALUES(?,?,?,?)", r) for r in [
                (706551971401695233, "achievement", "vcrgh", "Gryphon Heart"),
                (706551987799728168, "achievement", "extinguisher", "vSS HM"),
                (706551988667203634, "achievement", "godslayer", "Godslayer"),
                (706551977760260136, "role", "dps", "DPS"),
            ]])
        ids = [700000000000000000 + i for i in range(12)]
        m = lambda i: f"<@{ids[i]}>"
        parse = lambda text: parsing.parse_message(db, text)

        p = await parse("<@804188183889051698> healer <@&706551971401695233>")
        check("your existing one-person post still reads the same",
              p.assignments == [(804188183889051698, "healer")] and p.keys == ["vcrgh"],
              str(p.assignments))

        p = await parse(" ".join(m(i) for i in range(12)) + " dps <@&706551987799728168>")
        check("twelve people on one line all get the role",
              len(p.assignments) == 12 and {r for _, r in p.assignments} == {"dps"})

        run = ("<@&706551987799728168> <@&706551988667203634>\n"
               f"tank: {m(0)} {m(1)}\nhealer: {m(2)} {m(3)}\n"
               "dps: " + " ".join(m(i) for i in range(4, 12)))
        p = await parse(run)
        roles = dict(p.assignments)
        check("a full run assigns each person their own role",
              roles[ids[0]] == "tank" and roles[ids[3]] == "healer"
              and sum(1 for r in roles.values() if r == "dps") == 8)
        check("…and shares the achievements across all twelve",
              p.keys == ["extinguisher", "godslayer"])

        p = await parse(f"Tanks\n{m(0)} {m(1)}\nHealers\n{m(2)}")
        check("a role on its own line carries down to the lines below",
              dict(p.assignments) == {ids[0]: "tank", ids[1]: "tank", ids[2]: "healer"})

        p = await parse(f"{m(0)} {m(1)}\ndps <@&706551987799728168>")
        check("mentions with no role nearby take the post's only role",
              {r for _, r in p.assignments} == {"dps"})

        p = await parse(f"{m(0)} <@&706551977760260136> <@&706551987799728168>")
        check("a pinged @DPS role counts as the role word",
              p.assignments == [(ids[0], "dps")])

        p = await parse(f"{m(0)} tank {m(1)} healer <@&706551987799728168>")
        check("two roles on one line are left for an officer, not guessed",
              {r for _, r in p.assignments} == {""})

        p = await parse(f"tank: {m(0)}\ndps: {m(0)}\n<@&706551987799728168>")
        check("one person under two roles is left for an officer",
              p.assignments == [(ids[0], "")])

        p = await parse(run + "\nscore: 257,498")
        check("a score line gives the run's score", p.score == 257498)
        check("…and its trial is inferred from the achievements", p.score_trial == "vss")

        p = await parse(f"vss score 245,469k\ndps: {m(0)}")
        check("a named trial wins, and the guild's stray k is ignored",
              p.score == 245469 and p.score_trial == "vss", f"{p.score} {p.score_trial}")
        check("…and naming the trial doesn't grant its clear", p.keys == [])

        p = await parse(f"dps: {m(0)} {m(1)} - score to follow")
        check("people on a score line are never dropped",
              len(p.assignments) == 2, str(p.assignments))

        p = await parse(f"score: 250000\ndps: {m(0)}\nvkahm extinguisher")
        check("a score across two trials says it can't tell which",
              p.score_trial == "" and "which trial" in p.score_note)

        p = await parse("nice run everyone, what score did we get?")
        check("chatter that mentions a score is ignored", p.is_empty)
        await db.close()


# --- just enough of Discord to drive the channel reader end to end -----------
class FakeMember:
    def __init__(self, uid, roles=(), admin=False):
        from types import SimpleNamespace
        self.id, self.bot, self.roles = uid, False, list(roles)
        self.guild_permissions = SimpleNamespace(administrator=admin, manage_guild=False)
        self.display_name = self.name = f"user{uid}"
        self.global_name = None
        self.mention = f"<@{uid}>"
        self.display_avatar = SimpleNamespace(url="https://cdn.example/avatar.png")

    def __str__(self):
        return self.display_name


class FakeServer:
    def __init__(self, people):
        self.id = 1
        self.people = {p.id: p for p in people}
        self.members = list(people)

    def get_member(self, uid):
        return self.people.get(uid)


class FakeShot:
    content_type, size, filename, url = "image/png", 2048, "clear.png", "https://cdn.example/clear.png"

    async def to_file(self, filename=None):
        return discord.File(io.BytesIO(b"\x89PNG"), filename=filename or self.filename)


class FakeSent:
    def __init__(self, n, kwargs):
        from types import SimpleNamespace
        self.id, self.channel, self.kwargs = n, SimpleNamespace(id=2), kwargs
        self.jump_url = f"https://discord.com/channels/1/2/{n}"


class FakePost:
    counter = 1000

    def __init__(self, content, author, server, attachments=()):
        FakePost.counter += 1
        self.id = FakePost.counter
        self.content, self.author, self.guild = content, author, server
        self.attachments = list(attachments)
        self.jump_url = f"https://discord.com/channels/1/2/{self.id}"
        self.reactions, self.replies = [], []
        self.edited_at = None

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def reply(self, **kwargs):
        FakePost.counter += 1
        sent = FakeSent(FakePost.counter, kwargs)
        self.replies.append(sent)
        return sent


class FakeBot:
    def __init__(self, db):
        self.db, self.brand = db, embeds.Brand()
        self.overrides, self.cogs = {}, {}
        self.acts, self.problems = [], []
        self.channel = None

    async def setting(self, key):
        from unify.config import SETTING_DEFAULTS
        return self.overrides.get(key, SETTING_DEFAULTS[key][1])

    async def activity(self, action, detail):
        self.acts.append((action, detail))

    async def problem(self, title, detail):
        self.problems.append(title)

    async def sync_roles(self, *a, **k):
        pass

    async def audit(self, *a, **k):
        pass

    def get_user(self, uid):
        return None

    def get_cog(self, name):
        return self.cogs.get(name)

    async def channel_for(self, *keys):
        return self.channel


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        FakePost.counter += 1
        sent = FakeSent(FakePost.counter, kwargs)
        self.sent.append(sent)
        return sent


class FakeInteraction:
    """Records what a command said back, whether straight away or after deferring."""

    def __init__(self, user, client=None):
        from types import SimpleNamespace
        self.user, self.client, self.said, self.sent, self.deferred = user, client, [], [], False
        self.guild = None

        async def send_message(**kwargs):
            self.said.append(kwargs["embed"])
            self.sent.append(kwargs)

        async def defer(**kwargs):
            self.deferred = True

        async def send_modal(modal):
            self.sent.append({"modal": modal})

        self.response = SimpleNamespace(send_message=send_message, defer=defer,
                                        send_modal=send_modal)
        self.followup = SimpleNamespace(send=send_message)

    async def original_response(self):
        return None

    async def edit_original_response(self, **kwargs):
        self.sent.append(kwargs)


async def test_runs_end_to_end() -> None:
    """The bug this round fixed: a run posted in one message used to record its
    first person and silently drop the rest."""
    print("\nwhole runs, through the real channel reader")
    from unify.cogs.submissions import Submissions
    from unify.cogs.reviewqueue import approve_one
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        await db.run("INSERT INTO role_map VALUES(706551987799728168,'achievement','extinguisher','vSS HM')")
        LEAD_ROLE = 555000000000000001
        await db.set_setting("recorder_roles", [LEAD_ROLE])

        ids = [700000000000000000 + i for i in range(12)]
        raiders = []
        for i, d in enumerate(ids):
            raiders.append(await store.create_member(db, f"RAIDER{i:02d}.", d))
        lead = FakeMember(ids[0], roles=[type("R", (), {"id": LEAD_ROLE})()])
        outsider_lead = FakeMember(799999999999999999, roles=[type("R", (), {"id": LEAD_ROLE})()])
        pleb = FakeMember(ids[5])
        server = FakeServer([lead, outsider_lead, pleb])

        bot = FakeBot(db)
        bot.channel = FakeChannel()
        cog = Submissions(bot)
        bot.cogs["Submissions"] = cog
        mention = " ".join(f"<@{d}>" for d in ids)

        async def requests():
            return await db.all("SELECT * FROM requests WHERE status='pending'")

        # 1. a recorder posts a run they weren't in
        post = FakePost(f"{mention} dps <@&706551987799728168>", outsider_lead, server,
                        [FakeShot()])
        await cog.handle(post)
        held = [len(await store.owned(db, r.id, "dps")) for r in raiders]
        check("a lead posting a full run records all twelve, not one",
              all(n == 5 for n in held), f"recorded for {sum(1 for n in held if n)} of 12")
        check("…with nothing left for an officer", await requests() == [])
        check("…and one ✅", post.reactions == ["✅"], str(post.reactions))

        # 2. a recorder posts a run they were in: everyone else applies, they wait
        await db.run("DELETE FROM member_achievements")
        post = FakePost(f"{mention} dps <@&706551987799728168>", lead, server, [FakeShot()])
        await cog.handle(post)
        mine = await store.owned(db, raiders[0].id, "dps")
        others = [len(await store.owned(db, r.id, "dps")) for r in raiders[1:]]
        pending = await requests()
        check("a lead's own clear waits, even inside their own run",
              mine == set() and all(n == 5 for n in others))
        check("…as one request, not one per person", len(pending) == 1
              and json.loads(pending[0]["payload"])["members"] == [[raiders[0].id, "dps"]])

        # 3. an ordinary member posts the same run: one request for all of it
        await db.run("DELETE FROM member_achievements")
        await db.run("DELETE FROM requests")
        post = FakePost(f"{mention} dps <@&706551987799728168>", pleb, server, [FakeShot()])
        await cog.handle(post)
        pending = await requests()
        check("a member's run becomes ONE request for twelve people",
              len(pending) == 1 and len(json.loads(pending[0]["payload"])["members"]) == 12)
        check("…which writes nothing on its own",
              await db.val("SELECT COUNT(*) FROM member_achievements", (), 0) == 0)
        check("…and keeps the screenshot", pending[0]["proof_url"] == FakeShot.url)
        card_reply = post.replies[-1].kwargs
        check("…copied onto the card itself, not linked",
              "file" in card_reply and card_reply["embed"].image.url.startswith("attachment://"))

        result = await approve_one(bot, pending[0], FakeMember(1, admin=True), server)
        held = [len(await store.owned(db, r.id, "dps")) for r in raiders]
        check("one approval records the whole run",
              result.ok and all(n == 5 for n in held), result.summary)

        # 4. a repost of the same run is recognised
        await db.run("DELETE FROM member_achievements")
        await db.run("DELETE FROM requests")
        first = FakePost(f"{mention} dps <@&706551987799728168>", pleb, server)
        await cog.handle(first)
        again = FakePost(f"{mention}  dps <@&706551987799728168>", pleb, server)
        await cog.handle(again)
        check("posting the same run twice doesn't queue it twice",
              len(await requests()) == 1 and "♻️" in again.reactions)

        # 5. people who aren't on the roster are named, never silently skipped
        await db.run("DELETE FROM requests")
        stranger = 788888888888888888
        post = FakePost(f"<@{ids[1]}> <@{stranger}> tank <@&706551987799728168>",
                        outsider_lead, server, [FakeShot()])
        await cog.handle(post)
        said = post.replies[0].kwargs["embed"].description
        check("someone not on the roster is reported by name",
              f"<@{stranger}>" in said and "Not on the roster" in said)
        check("…while the person who is gets recorded",
              len(await store.owned(db, raiders[1].id, "tank")) == 5)
        check("…and the officers hear about it", bot.problems[-1].startswith("Submission names"))
        await db.close()


async def test_run_scores() -> None:
    print("\none score per run")
    from unify.cogs.submissions import Submissions
    from unify.cogs.reviewqueue import approve_one
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        LEAD_ROLE = 555000000000000001
        await db.set_setting("recorder_roles", [LEAD_ROLE])
        ids = [710000000000000000 + i for i in range(12)]
        raiders = [await store.create_member(db, f"R{i:02d}.", d) for i, d in enumerate(ids)]
        await store.set_score(db, raiders[3], "vss", 300000, 1, "t")   # already better

        outsider = FakeMember(799999999999999998, roles=[type("R", (), {"id": LEAD_ROLE})()])
        insider = FakeMember(ids[0], roles=[type("R", (), {"id": LEAD_ROLE})()])
        server = FakeServer([outsider, insider])
        bot = FakeBot(db)
        bot.channel = FakeChannel()
        cog = Submissions(bot)
        bot.cogs["Submissions"] = cog
        roster = "dps: " + " ".join(f"<@{d}>" for d in ids)

        await cog.handle(FakePost(f"vss score 257,498\n{roster}", outsider, server, [FakeShot()]))
        scores = [await db.val("SELECT score FROM scores WHERE member_id=? AND trial_key='vss'",
                               (r.id,)) for r in raiders]
        check("a recorder's run score lands on all twelve at once",
              sum(1 for v in scores if v == 257498) == 11, str(scores))
        check("…but never lowers a better score someone already had", scores[3] == 300000)

        await db.run("DELETE FROM scores")
        await cog.handle(FakePost(f"vss score 261,000\n{roster}", insider, server))
        pending = await db.all("SELECT * FROM requests WHERE status='pending'")
        check("a lead's score for a run they were in waits for an officer",
              await db.val("SELECT COUNT(*) FROM scores", (), 0) == 0 and len(pending) == 1)
        result = await approve_one(bot, pending[0], FakeMember(1, admin=True), server)
        check("…and one approval gives it to the whole run",
              result.ok and await db.val("SELECT COUNT(*) FROM scores WHERE score=261000", (), 0) == 12,
              result.summary)
        await db.close()


async def test_missing_role_is_fixable() -> None:
    """Approving a queued post with no role used to write achievements under an
    empty role that no profile could ever show."""
    print("\na missing role is caught, then fixable")
    from unify.cogs.submissions import Submissions
    from unify.cogs.reviewqueue import approve_one
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        await db.run("INSERT INTO role_map VALUES(706551987799728168,'achievement','extinguisher','vSS HM')")
        a = await store.create_member(db, "NOROLE.", 720000000000000001)
        poster = FakeMember(720000000000000009)
        bot = FakeBot(db)
        cog = Submissions(bot)
        bot.cogs["Submissions"] = cog
        await cog.handle(FakePost(f"<@{a.discord_id}> <@&706551987799728168>", poster,
                                  FakeServer([poster])))
        row = (await db.all("SELECT * FROM requests WHERE status='pending'"))[0]
        check("a post with no role is flagged as needing one", reviews.needs_role(row))
        refused = await approve_one(bot, row, FakeMember(1, admin=True), FakeServer([]))
        check("approving it without a role is refused", not refused.ok, refused.summary)
        check("…and wrote nothing",
              await db.val("SELECT COUNT(*) FROM member_achievements", (), 0) == 0)
        await reviews.set_role(db, row["id"], "healer")
        row = await reviews.get(db, row["id"])
        done = await approve_one(bot, row, FakeMember(1, admin=True), FakeServer([]))
        check("once a role is picked it records under that role",
              done.ok and len(await store.owned(db, a.id, "healer")) == 5)
        try:
            await store.grant(db, a, "", ["vss"], 1, "t")
            check("the core grant refuses an empty role outright", False)
        except ValueError:
            check("the core grant refuses an empty role outright", True)
        await db.close()


async def test_edits() -> None:
    """A second edit to the same post used to be ignored."""
    print("\nediting a post")
    from unify.cogs.submissions import Submissions
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        await db.run("INSERT INTO role_map VALUES(706551987799728168,'achievement','extinguisher','vSS HM')")
        await db.set_setting("submissions_channel", 2)
        a = await store.create_member(db, "EDIT A.", 730000000000000001)
        b = await store.create_member(db, "EDIT B.", 730000000000000002)
        c = await store.create_member(db, "EDIT C.", 730000000000000003)
        poster = FakeMember(730000000000000009)
        server = FakeServer([poster])
        bot = FakeBot(db)
        bot.overrides["submissions_channel"] = 2
        cog = Submissions(bot)
        bot.cogs["Submissions"] = cog

        post = FakePost(f"<@{a.discord_id}> dps <@&706551987799728168>", poster, server)
        post.channel = type("C", (), {"id": 2, "mention": "#subs"})()
        await cog.handle(post)

        async def edit(text):
            after = FakePost(text, poster, server)
            after.jump_url, after.channel = post.jump_url, post.channel
            after.edited_at = "just now"
            before = type("B", (), {"content": "previous"})()
            await cog.on_message_edit(before, after)

        await edit(f"<@{a.discord_id}> <@{b.discord_id}> dps <@&706551987799728168>")
        await edit(f"<@{a.discord_id}> <@{b.discord_id}> <@{c.discord_id}> dps <@&706551987799728168>")
        live = await db.all("SELECT payload FROM requests WHERE status='pending'")
        check("only the latest version of an edited post is waiting", len(live) == 1)
        check("a second edit is read, not ignored",
              len(json.loads(live[0]["payload"])["members"]) == 3,
              str(json.loads(live[0]["payload"])["members"]))
        await db.close()


async def test_evidence_on_cards() -> None:
    print("\nthe screenshot travels with every request")
    from unify.cogs.membership import Membership
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        bot = FakeBot(db)
        bot.channel = FakeChannel()
        bot.overrides["member_submissions"] = True
        cog = Membership(bot)
        newcomer = FakeMember(740000000000000001)

        class NotAPicture(FakeShot):
            content_type, filename = "application/pdf", "clear.pdf"

        class TooBig(FakeShot):
            size = 9 * 1024 * 1024

        for label, proof in (("no file", None), ("a pdf", NotAPicture()), ("a 9 MB image", TooBig())):
            said = FakeInteraction(newcomer)
            await cog.claim(said, "NEWBIE.", proof)
            check(f"a claim with {label} is turned away",
                  said.said and said.said[0].color == embeds.error("x").color
                  and await db.val("SELECT COUNT(*) FROM requests", (), 0) == 0)

        said = FakeInteraction(newcomer)
        await cog.claim(said, "NEWBIE.", FakeShot())
        row = await db.one("SELECT * FROM requests WHERE kind='register'")
        card_sent = bot.channel.sent[-1].kwargs
        check("a claim with a screenshot is raised", row is not None and said.deferred)
        check("…its card carries the screenshot itself",
              "file" in card_sent and card_sent["embed"].image.url == "attachment://proof.png")
        check("…and has no role picker, since a claim has no role",
              not any(isinstance(i, discord.ui.Select) for i in card_sent["view"].children))

        member = await store.create_member(db, "SCORER.", 740000000000000002)
        scorer = FakeMember(member.discord_id)
        said = FakeInteraction(scorer)
        await cog.submit_score.callback(cog, said, "vss", 250000, FakeShot())
        check("a /submit score card carries its screenshot",
              "file" in bot.channel.sent[-1].kwargs
              and await db.val("SELECT COUNT(*) FROM requests WHERE kind='score'", (), 0) == 1)
        said = FakeInteraction(scorer)
        await cog.submit_parse.callback(cog, said, "Arcanist", 120000, FakeShot())
        check("a /submit parse card carries its screenshot",
              "file" in bot.channel.sent[-1].kwargs
              and await db.val("SELECT COUNT(*) FROM requests WHERE kind='parse'", (), 0) == 1)
        await db.close()


async def test_recorder_week() -> None:
    print("\nthe weekly check on recorders")
    from unify.cogs.logs import recorder_week
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        check("a quiet week says nothing", await recorder_week(db) == [])

        FAIR, FRIENDLY = 750000000000000001, 750000000000000002
        group = [await store.create_member(db, f"RUN{i:02d}.", None) for i in range(12)]
        for m in group:
            await store.grant(db, m, "dps", ["vss"], FAIR, "fair lead")
        pals = group[:2]
        for m in pals:
            await store.grant(db, m, "tank", ["vasir"], FRIENDLY, "friendly lead")
            await store.grant(db, m, "healer", ["vasir"], FRIENDLY, "friendly lead")
        await store.grant(db, group[5], "dps", ["vas"], FRIENDLY, "friendly lead")
        await db.run("INSERT INTO members(gamertag) VALUES('OLD.')")
        old = await store.find_member(db, "OLD.")
        await store.grant(db, old, "dps", ["vss"], FAIR, "fair lead")
        await db.run("UPDATE member_achievements SET granted_at = datetime('now', '-9 days') "
                     "WHERE member_id = ?", (old.id,))

        lines = await recorder_week(db)
        fair = next(line for line in lines if str(FAIR) in line)
        friendly = next(line for line in lines if str(FRIENDLY) in line)
        check("each recorder gets one line", len(lines) == 2, str(lines))
        check("…counting what they recorded, for how many people",
              "for **12** people" in fair and "for **3** people" in friendly, fair)
        check("…and the titles among it", "· 4 titles" in friendly, friendly)
        check("recording a whole run evenly raises no flag", "⚠️" not in fair)
        check("recording the same few people over and over is flagged",
              "⚠️" in friendly and "RUN00." in friendly and "RUN01." in friendly, friendly)
        check("last week's work doesn't count this week", "for **13**" not in fair)
        await db.close()


async def test_status() -> None:
    print("\n/status in plain English")
    from types import SimpleNamespace
    from unify.cogs.settings import status_report
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        GUILD, SUBS, LOG = 760000000000000000, 760000000000000001, 760000000000000002
        everything = discord.Permissions(view_channel=True, read_message_history=True,
                                         send_messages=True, add_reactions=True,
                                         embed_links=True, attach_files=True)
        allowed = {"perms": everything}
        channels = {
            SUBS: SimpleNamespace(mention="#subs", permissions_for=lambda me: allowed["perms"]),
            LOG: SimpleNamespace(mention="#log"),
        }
        bot = FakeBot(db)
        bot.get_channel = channels.get
        guild = SimpleNamespace(id=GUILD, me=SimpleNamespace(
            guild_permissions=discord.Permissions(manage_roles=False)))

        def said(e, name):
            return next(f.value for f in e.fields if name in f.name)

        fresh = await status_report(bot, guild)
        check("a fresh install says nobody's posts are read",
              "nobody's posts are read" in said(fresh, "Fix"))
        check("…and that only officers record so far", "Recorders:** none" in said(fresh, "Who"))

        bot.overrides.update(submissions_channel=SUBS, audit_channel=LOG,
                             recorder_roles=[760000000000000009])
        good = await status_report(bot, guild)
        check("a sound setup says nothing needs fixing",
              "Nothing needs fixing" in said(good, "Problems"), said(good, "Problems"))
        check("…and explains who records without a review",
              "<@&760000000000000009>" in said(good, "Who")
              and "never for themselves" in said(good, "Who"))

        allowed["perms"] = discord.Permissions(view_channel=True, read_message_history=True,
                                               send_messages=True, embed_links=True)
        bot.overrides.update(recorder_roles=[GUILD], role_sync=True, submission_mode="review")
        bad = await status_report(bot, guild)
        problems = said(bad, "Fix")
        check("a permission the bot lacks in the channel is named",
              "react to posts" in problems and "copy screenshots" in problems, problems)
        check("@everyone as a recorder is called out", "@everyone is a recorder" in problems)
        check("role sync without Manage Roles is called out", "Manage Roles" in problems)
        check("review mode is explained", "**every** post waits" in said(bad, "Who"))

        await db.run("INSERT INTO members(gamertag, legacy_name) VALUES('MIGRATED.', 'migrated')")
        fresh_import = await status_report(bot, guild)
        check("a roster nobody's linked to yet points at /member match",
              "/member match" in said(fresh_import, "Fix"))

        bot.overrides["submissions_channel"] = 760000000000000099
        gone = await status_report(bot, guild)
        check("a deleted channel is noticed", "was deleted" in said(gone, "Fix")
              and "deleted channel" in said(gone, "Where"))
        await db.close()


async def test_settings_screen() -> None:
    print("\nthe settings screen")
    from unify.cogs.settings import GROUPS, Settings
    from unify.config import SETTING_DEFAULTS
    grouped = [k for keys in GROUPS.values() for k in keys]
    check("every setting is on the screen exactly once",
          sorted(grouped) == sorted(SETTING_DEFAULTS) and len(grouped) == len(set(grouped)),
          str(set(SETTING_DEFAULTS) ^ set(grouped)))
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        await db.set_setting("audit_days", 0)
        await db.set_setting("admin_roles", list(range(1, 8)))
        e = await Settings(FakeBot(db)).settings_screen()
        text = "\n".join(f.value for f in e.fields)
        check("settings are grouped, not one long list", len(e.fields) == len(GROUPS))
        check("a channel that must be set is flagged", "**submissions_channel**: ⚠️ **not set**" in text)
        check("0 is shown as 0, not as unset", "**audit_days**: `0`" in text)
        check("switches read on and off", "**registration**: ✅ on" in text)
        check("a long role list is shortened", "+2 more" in text)
        check("the whole screen fits in one message", len(e) <= 6000, str(len(e)))
        await db.close()


async def test_folded_commands() -> None:
    print("\nfewer commands, nothing lost")
    from unify import views
    from unify.cogs.guides import Guides
    from unify.cogs.profiles import Profiles
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        bot = FakeBot(db)
        member = await store.create_member(db, "FOLDED.", 770000000000000001)
        await store.set_score(db, member, "vss", 251000, 1, "t")
        await store.set_parse(db, member, "Arcanist", 118000, 1, "t")

        card = views.ProfileView(bot, 1, member)
        await card.build()
        labels = [getattr(i, "label", None) for i in card.children]
        check("the profile card has a Scores & parses button", "Scores & parses" in labels)
        card.numbers = True
        page = await card.build()
        text = "\n".join(f.name + f.value for f in page.fields)
        check("…which shows what /score and /parse did",
              "251,000" in text and "118,000" in text and "Arcanist" in text, text)
        check("…with a way back", [i.label for i in card.children] == ["Back to overview"])

        cog = Profiles(bot)
        stranger = FakeMember(770000000000000002)
        await reviews.open_request(db, "register", requester_id=stranger.id, subject="NEW.")
        said = FakeInteraction(stranger, bot)
        await cog.me.callback(cog, said)
        check("/me works before you're linked, privately",
              said.sent[0].get("ephemeral") and "not linked" in said.said[0].description)
        check("…and shows the claim you're waiting on",
              any("`#1` Roster claim" in f.value for f in said.said[0].fields),
              str([f.value for f in said.said[0].fields]))

        await reviews.open_request(db, "run", requester_id=999,
                                   payload={"keys": ["vss"], "members": [[member.id, "dps"]]})
        said = FakeInteraction(FakeMember(member.discord_id), bot)
        await cog.me.callback(cog, said)
        check("/me shows your card in the open", not said.sent[0].get("ephemeral"))
        check("…and privately, a run you're in that's still waiting",
              len(said.sent) == 2 and said.sent[1].get("ephemeral")
              and "#2" in said.said[1].description, said.said[-1].description)

        await db.run("INSERT INTO guides(category, topic, body) VALUES('cp', 'magblade', 'x')")
        guides = Guides(bot)
        said = FakeInteraction(stranger, bot)
        await guides.show.callback(guides, said)
        check("/guide show with no topic lists everything",
              "magblade" in said.said[0].fields[0].value)
        await db.close()


async def test_claim_button() -> None:
    print("\nthe welcome card's claim button")
    from unify.cogs.membership import Membership
    from unify.cogs.welcome import CLAIM_ID, ClaimModal, ClaimView, Welcome
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        bot = FakeBot(db)
        bot.channel = FakeChannel()
        bot.cogs["Membership"] = Membership(bot)
        welcome_channel = FakeChannel()
        bot.overrides["welcome_channel"] = 3
        bot.get_channel = {3: welcome_channel}.get

        view = ClaimView()
        check("the button survives a restart",
              view.is_persistent() and view.children[0].custom_id == CLAIM_ID)

        newcomer = FakeMember(780000000000000001)
        await Welcome(bot).on_member_join(newcomer)
        check("a newcomer's welcome card has the claim button",
              isinstance(welcome_channel.sent[-1].kwargs["view"], ClaimView))

        tapped = FakeInteraction(newcomer, bot)
        await view.claim.callback(tapped)
        check("tapping it opens the claim form", isinstance(tapped.sent[0].get("modal"), ClaimModal))

        form = ClaimModal()
        form.gamertag.component._value = "  NEWCOMER.  "
        form.proof.component._values = [FakeShot()]
        sent = FakeInteraction(newcomer, bot)
        await form.on_submit(sent)
        row = await db.one("SELECT * FROM requests WHERE kind = 'register'")
        check("sending the form raises the same claim /register does",
              row is not None and row["subject"] == "NEWCOMER." and row["requester_id"] == newcomer.id)
        check("…with the screenshot on the officers' card", "file" in bot.channel.sent[-1].kwargs)
        check("…and nothing is linked until an officer approves it",
              await store.find_member(db, newcomer.id) is None)

        tapped = FakeInteraction(newcomer, bot)
        await view.claim.callback(tapped)
        check("tapping again while waiting says so, without a form",
              "already have claim" in tapped.said[0].description and not any(
                  "modal" in c for c in tapped.sent))

        linked = FakeMember(780000000000000002)
        await store.create_member(db, "LINKED.", linked.id)
        await Welcome(bot).on_member_join(linked)
        check("someone already linked gets no button", welcome_channel.sent[-1].kwargs["view"] is None)
        tapped = FakeInteraction(linked, bot)
        await view.claim.callback(tapped)
        check("…and is told who they already are", "already **LINKED.**" in tapped.said[0].description)

        bot.overrides["registration"] = False
        await Welcome(bot).on_member_join(FakeMember(780000000000000003))
        check("with registration off there's no button", welcome_channel.sent[-1].kwargs["view"] is None)
        await db.close()


async def test_old_commands() -> None:
    """Officers will keep typing the old commands for a while. Each one gets the
    new slash command back, filled in, and nothing is ever run."""
    print("\nold ! commands")
    from types import SimpleNamespace
    from unify.cogs.oldcommands import OldCommands, translate
    from unify.cogs.submissions import Submissions
    cases = {
        # the guild's own sample, exactly as an officer types it today
        "!add_achievement [vcrgh][inkeddragonmami][healer]":
            "/achievement give member:inkeddragonmami role:healer achievements:vcrgh",
        "!add_achievement_g [vss vsshm] [BUDMAN008.] [Tank]":
            "/achievement give member:BUDMAN008. role:tank achievements:vss vsshm",
        "!remove_achievement [vka][budman008][dps]":
            "/achievement take member:budman008 role:dps achievements:vka",
        "!remove_achievement_g [VKA][BUDMAN008.][heals]":
            "/achievement take member:BUDMAN008. role:healer achievements:vka",
        "!info budman008": "/profile member:budman008",
        "!info_g BUDMAN008.": "/profile member:BUDMAN008.",
        "!clears budman008": "/profile member:budman008",
        "!clears_g BUDMAN008.": "/profile member:BUDMAN008.",
        "!tank budman008": "/profile member:budman008 role:tank",
        "!tank_g BUDMAN008.": "/profile member:BUDMAN008. role:tank",
        "!healer budman008": "/profile member:budman008 role:healer",
        "!healer_g BUDMAN008.": "/profile member:BUDMAN008. role:healer",
        "!dps budman008": "/profile member:budman008 role:dps",
        "!dps_g BUDMAN008.": "/profile member:BUDMAN008. role:dps",
        "!parse budman008": "/profile member:budman008",
        "!parse_g BUDMAN008.": "/profile member:BUDMAN008.",
        "!add_parse [95000] [Arcanist] [budman008]":
            "/record parse member:budman008 label:Arcanist dps:95000",
        "!add_parse_g [95000] [Arcanist] [BUDMAN008.]":
            "/record parse member:BUDMAN008. label:Arcanist dps:95000",
        "!update_name [BUDMAN008.] [budman008]": "/member link gamertag:BUDMAN008. user:",
        "!update_tag [budman008] [BUDMAN009.]":
            "/member rename gamertag:budman008 new_gamertag:BUDMAN009.",
        "!cp Magblade": "/guide show topic:cp/magblade",
        "!link gn vss": "/guide show topic:link/gn vss",
        "!export": "/export",
        "!command": "/help",
    }
    for old, new in cases.items():
        got = translate(old)
        check(f"{old.split()[0]:<22} becomes {got.command if got else '?'}",
              got is not None and got.new == new, got.new if got else "None")
    check("!parse points at the Scores & parses button",
          "Scores & parses" in translate("!parse budman008").hint)
    check("!update_name says who to pick", "budman008" in translate(
        "!update_name [BUDMAN008.] [budman008]").hint)
    for chatter in ("hello", "!play some song", "!!unused", "!infoo x",
                    "<@804188183889051698> healer <@&706551971401695233>"):
        check(f"{chatter!r} is left alone", translate(chatter) is None)

    with tempfile.TemporaryDirectory() as tmp:
        bot = UnifyBot(Env(token="x", guild_id=0, db_path=f"{tmp}/t.sqlite3"))
        await bot.db.connect()
        await bot.db.seed_catalog(CATALOG)
        for cog in COGS:
            await bot.load_extension(cog)
        real = {}
        for c in bot.tree.get_commands():
            for cmd in (c.commands if hasattr(c, "commands") else [c]):
                name = f"/{c.name} {cmd.name}" if hasattr(c, "commands") else f"/{c.name}"
                real[name] = {p.name for p in cmd.parameters}
        bad = []
        for old in cases:
            found = translate(old)
            options = {o.split(":", 1)[0] for o in found.options.split() if ":" in o}
            if found.command not in real or not options <= real[found.command]:
                bad.append(found.new)
        check("every translation names a real command and real options", not bad, str(bad))
        for cog in reversed(COGS):
            await bot.unload_extension(cog)
        await bot.db.close()

    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        bot = FakeBot(db)
        bot.overrides["submissions_channel"] = 2
        cog = OldCommands(bot)
        officer = FakeMember(790000000000000001)
        post = FakePost("!add_achievement [vcrgh][inkeddragonmami][healer]", officer, FakeServer([]))
        post.channel = SimpleNamespace(id=2)
        await cog.on_message(post)
        said = post.replies[0].kwargs["embed"].description if post.replies else ""
        check("the reply carries the filled-in command",
              "/achievement give member:inkeddragonmami role:healer achievements:vcrgh" in said)
        check("…and says nothing was changed", "Nothing was changed" in said)
        check("…and nothing was", await db.val("SELECT COUNT(*) FROM member_achievements", (), 0) == 0
              and await db.val("SELECT COUNT(*) FROM requests", (), 0) == 0)
        check("the submissions reader leaves it alone",
              not await Submissions(bot).watched(post))
        again = FakePost("!info budman008", officer, FakeServer([]))
        await cog.on_message(again)
        check("one person can't make the bot spam", again.replies == [])
        await db.close()


async def test_dot_claims() -> None:
    """96% of the roster ends in a dot, and members type their gamertag without
    it. A claim used to miss their record and create an empty copy."""
    print("\nclaims and the trailing dot")
    from unify.cogs.membership import Membership
    from unify.cogs.reviewqueue import approve_one, describe
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        bot = FakeBot(db)
        bot.channel = FakeChannel()
        cog = Membership(bot)
        veteran = await store.create_member(db, "BUDMAN008.", None)
        await store.grant(db, veteran, "dps", ["vss"], 1, "t")
        before = await db.val("SELECT COUNT(*) FROM members", (), 0)

        check("the dot is forgiven", [m.gamertag for m in await store.same_gamertag(db, "budman008")]
              == ["BUDMAN008."])
        check("…and nothing else is", await store.same_gamertag(db, "BUDMAN00") == []
              and await store.same_gamertag(db, "BUDMAN0088") == [])

        member = FakeMember(791000000000000001)
        said = FakeInteraction(member, bot)
        await cog.claim(said, "BUDMAN008", FakeShot())
        row = await db.one("SELECT * FROM requests WHERE kind = 'register'")
        check("a claim without the dot names the roster's record",
              row["subject"] == "BUDMAN008." and row["note"] == "claims an existing record",
              f"{row['subject']} / {row['note']}")
        check("…and tells the member how the roster spells it",
              "written on the roster" in said.said[-1].description)
        line = await describe(bot, row)
        check("the officer's card shows what it would inherit", "would inherit" in line, line)
        result = await approve_one(bot, row, FakeMember(1, admin=True), FakeServer([]))
        linked = await store.find_member(db, member.id)
        check("approving it links the existing record, with its clears",
              result.ok and linked is not None and linked.id == veteran.id, result.summary)
        check("…and creates no copy", await db.val("SELECT COUNT(*) FROM members", (), 0) == before)

        upsert = await store.upsert_member(db, "budman008", None)
        check("/member add without the dot finds the record too",
              not upsert.created and upsert.member.id == veteran.id)

        await store.create_member(db, "a hos3r eh", None)
        await store.create_member(db, "a hos3r eh.", None)
        other = FakeMember(791000000000000002)
        said = FakeInteraction(other, bot)
        await cog.claim(said, "a hos3r eh", FakeShot())
        row = await db.one("SELECT * FROM requests WHERE kind = 'register' AND requester_id = ?",
                           (other.id,))
        line = await describe(bot, row)
        check("a claim that matches two dot-twins warns the officer", "merge" in line, line)
        refused = await approve_one(bot, row, FakeMember(1, admin=True), FakeServer([]))
        check("…and can't be approved until they're merged",
              not refused.ok and "/member merge" in refused.summary, refused.summary)

        taken = await store.create_member(db, "TAKEN.", 791000000000000099)
        said = FakeInteraction(FakeMember(791000000000000003), bot)
        await cog.claim(said, "taken", FakeShot())
        check("a record linked to someone else can't be claimed by leaving the dot off",
              "already linked" in said.said[-1].description
              and await store.find_member(db, 791000000000000003) is None and taken)
        await db.close()


async def test_rejoining() -> None:
    """Leaving switched a member off, and nothing ever switched them back on, so a
    returning raider vanished from /find and the leaderboards for good."""
    print("\nleaving and coming back")
    from unify import views
    from unify.cogs.admin import Admin
    from unify.cogs.membership import Membership
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        bot = FakeBot(db)
        cog = Membership(bot)
        raider = await store.create_member(db, "RETURNER.", 792000000000000001)
        person = FakeMember(raider.discord_id)
        person.guild = None

        async def active(member_id):
            return await db.val("SELECT active FROM members WHERE id = ?", (member_id,))

        await cog.on_member_remove(person)
        check("leaving the server switches them off", await active(raider.id) == 0)
        await cog.on_member_join(person)
        check("coming back switches them on again", await active(raider.id) == 1)
        check("…and says so in the activity log", bot.acts[-1][0] == "member returned")

        class Yes:
            def __init__(self, *a, **k):
                self.result = True

            async def wait(self):
                return None

        removed = await store.create_member(db, "REMOVED.", 792000000000000002)
        officer = FakeInteraction(FakeMember(1, admin=True), bot)
        real_confirm, views.Confirm = views.Confirm, Yes
        try:
            await Admin(bot).member_remove.callback(Admin(bot), officer, "REMOVED.")
        finally:
            views.Confirm = real_confirm
        check("an officer can still remove someone", await active(removed.id) == 0)
        leaver = FakeMember(removed.discord_id)
        leaver.guild = None
        await cog.on_member_remove(leaver)
        await cog.on_member_join(leaver)
        check("…and leaving and rejoining doesn't undo that", await active(removed.id) == 0)
        entry = await db.val("SELECT id FROM audit_log WHERE action = 'member remove'")
        await store.undo_action(db, entry)
        check("/undo of the removal puts them back as they were", await active(removed.id) == 1)
        await db.close()


async def test_recorder_safeguards() -> None:
    """The last ways a trusted recorder could slip somebody in and leave nothing
    behind to check."""
    print("\nsafeguards on instant recordings")
    import unify.cogs.submissions as submissions
    from unify.cogs.logs import recorder_week
    from unify.cogs.reviewqueue import approve_one, reject_one
    with tempfile.TemporaryDirectory() as tmp:
        db = await fresh_db(f"{tmp}/t.sqlite3")
        await db.run("INSERT INTO role_map VALUES(706551987799728168,'achievement','extinguisher','vSS HM')")
        LEAD_ROLE = 555000000000000001
        await db.set_setting("recorder_roles", [LEAD_ROLE])
        a = await store.create_member(db, "SAFE A.", 793000000000000001)
        b = await store.create_member(db, "SAFE B.", 793000000000000002)
        c = await store.create_member(db, "SAFE C.", 793000000000000003)
        lead = FakeMember(793000000000000009, roles=[type("R", (), {"id": LEAD_ROLE})()])
        server = FakeServer([lead])
        bot = FakeBot(db)
        cog = submissions.Submissions(bot)
        bot.cogs["Submissions"] = cog
        officer = FakeMember(1, admin=True)

        async def pending():
            return await db.all("SELECT * FROM requests WHERE status = 'pending' ORDER BY id")

        async def clears(member):
            return len(await store.owned(db, member.id, "dps"))

        async def wipe():
            await db.run("DELETE FROM member_achievements")
            await db.run("DELETE FROM requests")
            await db.run("DELETE FROM scores")

        # no screenshot: waits, even from a recorder
        bot.channel = FakeChannel()
        await cog.handle(FakePost(f"<@{a.discord_id}> dps <@&706551987799728168>", lead, server))
        rows = await pending()
        check("a recorder's post with no screenshot waits for an officer",
              await clears(a) == 0 and len(rows) == 1 and "no screenshot" in rows[0]["note"],
              rows[0]["note"] if rows else "nothing queued")

        # nowhere to keep the copy: waits
        await wipe()
        bot.channel = None
        await cog.handle(FakePost(f"<@{a.discord_id}> dps <@&706551987799728168>", lead, server,
                                  [FakeShot()]))
        rows = await pending()
        check("with nowhere to keep a copy of the screenshot, it waits too",
              await clears(a) == 0 and rows and "nowhere to keep a copy" in rows[0]["note"])

        # the copy is kept, and every change links to it
        await wipe()
        bot.channel = FakeChannel()
        post = FakePost(f"<@{a.discord_id}> <@{b.discord_id}> dps <@&706551987799728168>", lead,
                        server, [FakeShot()])
        await cog.handle(post)
        copy = bot.channel.sent[0]
        links = {r[0] for r in await db.all("SELECT evidence FROM audit_log WHERE action = 'grant'")}
        check("an instant recording keeps the bot's own copy of the screenshot",
              "file" in copy.kwargs and copy.kwargs["embed"].image.url.startswith("attachment://"))
        check("…and links every change to that copy", links == {copy.jump_url}, str(links))
        check("…and the activity digest links it too", copy.jump_url in bot.acts[-1][1])

        # an edit adds somebody: they wait
        edited = FakePost(f"<@{a.discord_id}> <@{b.discord_id}> <@{c.discord_id}> dps "
                          "<@&706551987799728168>", lead, server, [FakeShot()])
        edited.jump_url, edited.edited_at = post.jump_url, "just now"
        edited.channel = type("C", (), {"id": 2, "mention": "#subs"})()
        bot.overrides["submissions_channel"] = 2
        await cog.on_message_edit(type("B", (), {"content": "before"})(), edited)
        rows = await pending()
        check("somebody added by an edit waits for an officer",
              await clears(c) == 0 and rows and "added by an edit" in rows[-1]["note"]
              and json.loads(rows[-1]["payload"])["members"] == [[c.id, "dps"]])
        check("…while the people already recorded stay recorded",
              await clears(a) == 5 and await clears(b) == 5)

        # a new guild best waits
        await wipe()
        await store.set_score(db, c, "vss", 200000, 1, "t")
        roster = f"dps: <@{a.discord_id}> <@{b.discord_id}>"
        await cog.handle(FakePost(f"vss score 2,574,980\n{roster}", lead, server, [FakeShot()]))
        rows = await pending()
        check("a score that would beat the guild best waits for an officer",
              await db.val("SELECT COUNT(*) FROM scores", (), 0) == 1
              and rows and "new guild best" in rows[0]["note"], rows[0]["note"] if rows else "")
        await db.run("DELETE FROM requests")
        await cog.handle(FakePost(f"vss score 190,000\n{roster}", lead, server, [FakeShot()]))
        check("…while an ordinary score still records straight away",
              await db.val("SELECT COUNT(*) FROM scores WHERE score = 190000", (), 0) == 2)

        # spot checks: Reject undoes the whole post, Approve changes nothing
        await wipe()
        await store.set_score(db, c, "vss", 200000, 1, "t")      # so 150,000 isn't a guild best
        await store.set_score(db, b, "vss", 100000, 1, "t")      # b's old score comes back
        submissions.SPOT_CHECK_RATE, rate = 1.0, submissions.SPOT_CHECK_RATE
        try:
            bot.channel = FakeChannel()
            await cog.handle(FakePost(f"vss score 150,000\n<@{a.discord_id}> <@{b.discord_id}> "
                                      "dps <@&706551987799728168>", lead, server, [FakeShot()]))
            check("…the run recorded straight away, score and all",
                  await clears(a) == 5 and await db.val(
                      "SELECT COUNT(*) FROM scores WHERE score = 150000", (), 0) == 2)
            check_row = await db.one("SELECT * FROM requests WHERE kind = 'check'")
            card_sent = bot.channel.sent[-1].kwargs
            check("an instant recording can be picked for a spot check",
                  check_row is not None and "file" in card_sent
                  and "Reject** undoes" in "".join(f.value for f in card_sent["embed"].fields))
            summary = await reject_one(bot, check_row, officer, server)
            scores = {r[0]: r[1] for r in await db.all("SELECT member_id, score FROM scores")}
            check("rejecting the spot check undoes everything that post recorded",
                  await clears(a) == 0 and await clears(b) == 0 and a.id not in scores
                  and summary == "undid 4 changes", summary)
            check("…putting back the scores it replaced, and nobody else's",
                  scores.get(b.id) == 100000 and scores.get(c.id) == 200000, str(scores))

            await wipe()
            await cog.handle(FakePost(f"<@{a.discord_id}> dps <@&706551987799728168>", lead,
                                      server, [FakeShot()]))
            check_row = await db.one("SELECT * FROM requests WHERE kind = 'check'")
            result = await approve_one(bot, check_row, officer, server)
            check("approving a spot check leaves the recording alone",
                  result.ok and await clears(a) == 5)
        finally:
            submissions.SPOT_CHECK_RATE = rate

        # an approval links the card the officer looked at
        await wipe()
        member_post = FakePost(f"<@{a.discord_id}> dps <@&706551987799728168>", FakeMember(5),
                               server, [FakeShot()])
        await cog.handle(member_post)
        row = (await pending())[0]
        await approve_one(bot, row, officer, server)
        link = await db.val("SELECT evidence FROM audit_log WHERE action = 'grant' "
                            "ORDER BY id DESC LIMIT 1")
        check("an approved request links to its card",
              link == f"https://discord.com/channels/1/{row['channel_id']}/{row['message_id']}",
              link)

        # the Monday check counts scores
        await store.set_score(db, b, "vss", 123, lead.id, "lead")
        week = await recorder_week(db)
        check("the Monday check counts scores too",
              any(str(lead.id) in line and "score" in line for line in week), str(week))
        await db.close()


async def main() -> None:
    await test_command_tree()
    await test_prerequisites()
    await test_message_parsing()
    await test_import()
    await test_identity_safety()
    await test_display_limits()
    await test_role_sync()
    await test_find()
    await test_marks_and_formats()
    await test_name_matching()
    await test_guild_only()
    await test_export_round_trip()
    await test_trust_routing()
    await test_review_queue()
    await test_roster_claims()
    await test_submission_modes()
    await test_member_input_is_inert()
    await test_leaderboard_top_n()
    test_gamertag_validation()
    await test_name_lookup()
    await test_run_format()
    await test_runs_end_to_end()
    await test_run_scores()
    await test_missing_role_is_fixable()
    await test_edits()
    await test_evidence_on_cards()
    await test_recorder_week()
    await test_status()
    await test_settings_screen()
    await test_folded_commands()
    await test_claim_button()
    await test_old_commands()
    await test_dot_claims()
    await test_rejoining()
    await test_recorder_safeguards()
    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
