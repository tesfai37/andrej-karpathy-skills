# Unify Bot — Officer Guide

How to run the bot. Assumes no coding: everything below happens inside Discord,
except the one-time install.

---

## 1. Install (once, by one person)

### The bot account

1. <https://discord.com/developers/applications> → **New Application** → name it `Unify`
2. **Bot** → **Reset Token** → copy it
3. Same page, **Privileged Gateway Intents** — turn **both** on:
   - **Server Members Intent** — without it the bot can't see who's who, and
     `/member match` has nothing to match against
   - **Message Content Intent** — without it the bot can't read the achievements
     channel at all

### Inviting it

**OAuth2 → URL Generator**:

- **Scopes:** `bot`, `applications.commands`
- **Permissions:** View Channels, Send Messages, Send Messages in Threads, Embed
  Links, Attach Files, Read Message History, Add Reactions, Use External Emojis,
  **Manage Roles**

> Manage Roles is only needed for `/sync`. Discord also requires the bot's own
> role to sit **above** any role it hands out — drag it up in
> Server Settings → Roles. `/sync check` tells you if it isn't.

### Running it

```bash
cd unify-bot
pip install -r requirements.txt
cp .env.example .env        # put your token and server ID in this file
python main.py
```

`GUILD_ID` is your server's ID (right-click the server icon → Copy Server ID; turn
on Developer Mode under Settings → Advanced if you don't see it). Setting it makes
slash commands appear instantly instead of after an hour.

`docker compose up -d` does the same and restarts the bot if the machine reboots.

---

## 2. Set it up — `/setup`

One screen, four dropdowns, no typing:

1. **Submissions channel** — where people post clears
2. **Admin log channel** — where the bot writes what changed
3. **Admin roles** — who is allowed to change data
4. **Auto-map roles** — matches your existing Discord roles to achievements by name

Server administrators always count as admins, so you can't lock yourself out.

---

## 3. Teach it your roles

This is what makes the channel reading work. Three ways in:

```
/map auto        ← scans every role, shows the list, waits for you to confirm
/map role  @vSS Ice HM     ← two dropdowns: pick trial, pick achievement
/map set   @role  vssice   ← one line if you know the code
```

Check your work:

```
/map missing     ← achievements that still have no Discord role
/map list        ← every mapping you have
/map remove      ← forget one
```

`/map auto` never saves without showing you the matches first. Anything it guesses
wrong you fix with `/map role`.

---

## 4. Get your data in

### From the old database

```bash
python tools/migrate_legacy.py /path/to/database.db
python tools/verify_migration.py /path/to/database.db data/unify.sqlite3
```

The first reads your old `TANK` / `HEALER` / `DPS` / `AWA` / `SCORE` / `PARSE`
tables plus the CP, LINK, SETUP, CRAFT and FARMING reference tables. It never
writes to the old file. It reports, rather than guessing:

- how many gamertags appeared twice and were merged, and where duplicate rows disagreed
- cells that weren't `X`, `L` or blank (notes like `banned`, `NA/EU`) — skipped, listed
- records that tick a hard mode without its bosses — copied as-is, counted
- columns it didn't recognise

The second proves nothing was lost: it counts every cell in the old file and
compares against what came out. Read-only on both.

### Linking Discord accounts

The old file stored display names, not account IDs, so nobody is linked after a
migration. Fix it in one command:

```
/member match
```

It looks every saved name up against your server, takes only exact unambiguous
matches, shows you a preview, and skips any account two roster entries both claim.
`/undo` reverses the whole batch.

### From a spreadsheet

```
/import    ← then drag the .xlsx or .csv onto Discord
```

It tells you what it found before doing anything — which sheet maps to which role,
how many achievement columns, what it will ignore — and waits for **Import**.

A bulk import is **not** covered by `/undo`. Take an `/export` first.

---

## 5. Day-to-day

### Approving clears

With `submission_mode` on `review` (the default), each post gets an
**Approve / Reject** card. Approve records it and DMs the member. The role dropdown
on the card lets you correct the role before approving.

Switch to instant recording with `/config set key:submission_mode value:auto`.

### Fixing records by hand

```
/achievement give   member: BUDMAN008  role: DPS  achievements: extinguisher
/achievement take   member: BUDMAN008  role: DPS  achievements: vss
/record score       member: BUDMAN008  trial: vss  score: 257498
/record parse       member: BUDMAN008  label: Arcanist  dps: 118000
```

Giving fills in prerequisites automatically. Taking cascades the other way — remove
`vss` and everything that depended on it goes too, so nobody is ever left claiming
a hard mode without the clear.

### The roster

```
/member add     gamertag: NewGuy.  user: @newguy
/member link    gamertag: NewGuy.  user: @newguy
/member rename  gamertag: OldName.  new_gamertag: NewName.
/member remove  gamertag: Leaver.        ← keeps their record, hides them from lists
```

The bot never renames one member to make room for another. If a gamertag and a
Discord account disagree it says so and changes nothing.

---

## 6. Handing out roles automatically

Your Discord roles already say who cleared what. `/sync` keeps them that way.

**Off until you turn it on** — the first run can hand out hundreds of roles.

```
/sync check                            ← always start here
/config set key:role_sync value:true
/sync all                              ← previews, then waits for you to confirm
```

`/sync check` reports the only two things that go wrong: whether the bot has
**Manage Roles**, and which achievement roles sit **above** it. It names them.

After that, roles follow every grant — from the channel, from `/achievement give` —
and come back off when an achievement is removed. `/sync member` fixes one person.

**Only roles in `/map list` are ever touched.** Colour roles, ping roles, raid
roles: untouched.

A bulk `/import` doesn't sync inline — that would be thousands of role changes at
once. It reminds you to run `/sync all`.

---

## 7. New content

No code, no restart, no waiting for a developer:

```
/trial add        key: vnew   short: vNEW   name: Some Trial   emoji: 🦴
/achievement new  trial: vNEW  key: vnewhm  name: Some Trial HM  kind: Full hard mode  requires: vnew
/achievement rename  achievement: vsetwelv  name: The Real Boss Name
/map role         role: @vNEW HM
```

`kind` drives the leaderboard weighting: Trial clear 1, Boss hard mode 3, Full
hard mode 5, Title 10. `requires` is what must come with it.

The emoji box only accepts a real emoji — a word there would break `/profile` for
the whole server, so the bot refuses it.

---

## 8. Guild reference — `/guide`

The CP builds and channel lists members read with `/guide show`.

```
/guide save    category: cp  topic: magcro     ← opens a multi-line text box
/guide delete  topic: cp · magcro
```

`/guide save` on an existing entry pre-fills the current text so you can edit it.

---

## 9. Backups

```
/export
```

Three shapes, **not interchangeable**:

| Format | What it's for |
| --- | --- |
| **Excel workbook** | The layout you know — sheet per role, `X` and `L` cells, plus Score, Parse and your guides. This is the one `/import` reads back, so it's a working restore. |
| **CSV files** | Raw dump of every table, zipped. For reading, not restoring. |
| **Raw database** | The live `.sqlite3`. Most complete: stop the bot, drop it in place of `data/unify.sqlite3`, start again. |

A weekly copy posts itself to `reports_channel` — set the day and hour with
`report_day` and `report_hour`, or `off` to stop it.

---

## 10. When something goes wrong

```
/audit           ← every change, who made it, when
/undo            ← reverse the last one
/undo 42         ← reverse a specific entry from /audit
```

Every admin action writes its own inverse, so `/undo` is a real undo, not a guess.
Bulk imports are the exception.

| Symptom | Fix |
| --- | --- |
| Slash commands don't appear | Check `GUILD_ID` in `.env`, restart. Without it they take up to an hour. |
| Bot ignores the achievements channel | `/config show` — is `submissions_channel` set? Is **Message Content Intent** on? |
| "I don't know what @X means yet" | `/map role` on that role, or press the button in the message |
| "I couldn't tell who this is for" | `/member add` them, or `/member link` |
| `/member match` says it can only see itself | **Server Members Intent** is off. Turn it on, restart. |
| `/sync` skips roles | `/sync check` — the bot's role needs to sit above them |
| An achievement's name is wrong | `/achievement rename` |
| Wrong data got in | `/undo`, or `/audit` then `/undo 42` |

---

## Settings reference

`/config show` lists these with their current values. Channels use
`/config channel`, role lists use `/config roles`, everything else `/config set`.

| Key | Default | What it controls |
| --- | --- | --- |
| `guild_name` | Unify | Name on every embed |
| `brand_color` | `#8B5CF6` | Accent colour |
| `logo_url` | — | Guild logo thumbnail |
| `submissions_channel` | — | Channel the bot watches |
| `audit_channel` | — | Where changes are logged |
| `welcome_channel` | — | Welcome card on join |
| `reports_channel` | — | Weekly report and backup |
| `admin_roles` | — | Who can change data |
| `viewer_roles` | — | Who can look people up (empty = everyone) |
| `submission_mode` | `review` | `review` or `auto` |
| `notify_dm` | `true` | DM members when they earn something |
| `mark_label` | `Legacy` | Name for the `L` marker |
| `role_sync` | `false` | Hand out Discord roles automatically |
| `report_day` | `sun` | Weekly report day, or `off` |
| `report_hour` | `18` | Hour (0–23, UTC) |

---

## Checking the bot still works after a change

```bash
python tests/smoke.py
```

104 checks, no Discord connection needed. It builds the whole command tree and
exercises prerequisite chains, message parsing, spreadsheet import, role-sync
arithmetic and a full export → import round trip.
