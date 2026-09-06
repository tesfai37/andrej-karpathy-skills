"""Prove a migration lost nothing.

    python tools/verify_migration.py <old database.db> <new unify.sqlite3>

Counts every X and L cell in the old wide tables - merging duplicate gamertags
the same way the migration does - and compares them against the rows that came
out the other side. Read-only on both files."""
import sqlite3, sys, collections
old = sqlite3.connect(sys.argv[1]); old.row_factory = sqlite3.Row
new = sqlite3.connect(sys.argv[2]); new.row_factory = sqlite3.Row

print("=== duplicate gamertags in the source ===")
for t in ("TANK","DPS"):
    tag = [d[1] for d in old.execute('PRAGMA table_info("%s")' % t)][0]
    dups = old.execute(
        'SELECT LOWER(TRIM("%s")) g, COUNT(*) n FROM "%s" '
        'WHERE TRIM("%s")<>\'\' GROUP BY g HAVING n>1 ORDER BY n DESC LIMIT 5' % (tag,t,tag)).fetchall()
    total = old.execute('SELECT COUNT(*) FROM (SELECT LOWER(TRIM("%s")) g FROM "%s" '
                        'WHERE TRIM("%s")<>\'\' GROUP BY g HAVING COUNT(*)>1)' % (tag,t,tag)).fetchone()[0]
    print("  %s: %d gamertags appear more than once, e.g. %s" %
          (t, total, [(r["g"], r["n"]) for r in dups]))

print("\n=== source counts AFTER merging duplicate gamertags ===")
ROLES = {"TANK":"tank","HEALER":"healer","DPS":"dps","AWA":"account"}
allok = True
for table, role in ROLES.items():
    cols = [d[1] for d in old.execute('PRAGMA table_info("%s")' % table)][2:]
    merged, conflicts = {}, 0
    for row in old.execute('SELECT * FROM "%s"' % table):
        tag = str(row[0] or "").strip().lower()
        if not tag:
            continue
        for c in cols:
            v = str(row[c] or "").strip().upper()
            if v in ("X","L"):
                key = (tag, c)
                if key in merged and merged[key] != v:
                    conflicts += 1
                if key not in merged or (merged[key] == "L" and v == "X"):
                    merged[key] = v          # a modern clear beats a legacy record
    src = collections.Counter(merged.values())
    dst = dict(new.execute("SELECT mark, COUNT(*) FROM member_achievements WHERE role=? "
                           "GROUP BY mark", (role,)).fetchall())
    same = src["X"] == dst.get("X",0) and src["L"] == dst.get("L",0)
    allok &= same
    print("  %-8s X=%-6d L=%-6d  ->  X=%-6d L=%-6d  %s  (%d cells where duplicate rows disagreed)"
          % (table, src["X"], src["L"], dst.get("X",0), dst.get("L",0),
             "OK" if same else "STILL OFF", conflicts))

print("\nOVERALL:", "every cell accounted for" if allok else "still a gap")
