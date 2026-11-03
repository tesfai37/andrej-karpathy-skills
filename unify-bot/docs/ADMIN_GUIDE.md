# Unify Bot — Officer Guide

How to run the bot. Assumes no coding: everything below happens inside Discord,
except the one-time install.

---

## Switching over from the old bot

Do this once, in this order:

1. **Turn the old bot off.** From here on, anything typed into it is lost: the
   new bot only *translates* the old `!` commands, it never runs them.
2. **Take the newest copy of the old `database.db`.** If you have more than one
   copy, make sure it's the most recent, or the clears since then are missing.
3. **Migrate it and check it** (section 4).
4. **Start the new bot**, run `/setup` and `/map auto` (sections 2 and 3).
5. **Merge duplicate records** with `/member merge`, and take junk rows off with
   `/member remove` (section 5, *The roster*).
6. **Run `/member match`** so posts can find people (section 4).
7. **Run `/status`** and fix anything it lists.
8. **Tell your members**: post the way you always have, attach the screenshot,
   and tap **Claim my gamertag** on the welcome card if you aren't linked.

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

One screen, no typing:

1. **Submissions channel** — where people post clears
2. **Admin log channel** — where the bot writes what changed
3. **Officer roles** — who is allowed to change data
4. **Recorder roles** — raid leads, who may record for *other* people
5. **Auto-map roles** — matches your existing Discord roles to achievements by name

Server administrators always count as officers, so you can't lock yourself out.
The activity and problem log channels are set separately with `/config channel`.

Then run:

```
/status
```

It says in plain English who can record, what members can do and where
everything goes, and lists anything that's quietly broken: no submissions
channel, a channel that was deleted, a permission the bot is missing, @everyone
picked as an officer or recorder role, or a roster nobody is linked to yet.
Run it again whenever something seems off.

**Members don't see officer commands.** Discord hides them from anyone without
**Manage Server**, so a member's `/` menu only lists what they can use. If an
officer gets their access only from an officer role, the server owner shows the
commands to that role once: Server Settings, then Integrations, then Unify, and
allow the role. The bot still checks every use either way. `/guide` stays
visible to everyone, because members use `/guide show`.

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
migration. **Do this before anyone posts**: until it's done, every post that
@mentions people comes back "not on the roster". Fix it in one command:

```
/member match
```

It looks every saved name up against your server, takes only exact unambiguous
matches, shows you a preview, and skips any account two roster entries both claim.
`/undo` reverses the whole batch. Anyone it can't match can claim their gamertag
themselves, or you link them with `/member link`.

**Merge duplicates first.** Two records that differ only by a trailing dot, like
`a hos3r eh` and `a hos3r eh.`, are one person, and match skips a Discord name
that two records both claim. `/member merge` folds one into the other first.

Looking people up works by Discord name straight away, linked or not: typing
`budman008` into `/profile` or any officer command finds BUDMAN008. from the
saved name.

### From a spreadsheet

```
/import    ← then drag the .xlsx or .csv onto Discord
```

It tells you what it found before doing anything — which sheet maps to which role,
how many achievement columns, what it will ignore — and waits for **Import**.

A bulk import is **not** covered by `/undo`, so the bot saves a full backup of
the database right before every import starts (section 9). If it can't save the
backup, it doesn't import.

---

## 5. Day-to-day

### The queue is the job

Everything waiting on you — submissions, roster claims, member-sent scores and
parses — is in one place:

```
/queue                  ← everything
/queue kind: Roster claim
```

Eight at a time, with **Approve this page**, or pick individual ones from the
menu and **Approve selected**. Each entry says why it needs you, and 📎 marks
the ones with a screenshot.

You can also approve from the **card**, without running anything. A channel
post gets its card as a reply right under the post; `/register` and `/submit`
cards go to your admin log channel. Both routes go through the same code.

**A whole run is one card.** It lists everyone in the post under their role,
the achievements and the run's score, with the screenshot copied onto the card
itself, since Discord's own attachment links expire. One **Approve** records
the whole run.

**A missing role is never guessed.** If anyone in the post has no clear role,
the card says *No clear role — pick one below* and shows a Tank / Healer / DPS
menu. Approve refuses until a role is picked. In `/queue` the same menu sets
the role for everything you've selected.

Two more sorts of card land in your admin log channel:

- **🔍 Spot check.** About one recording in twenty that went in straight away
  comes back to you afterwards, with its screenshot. **Approve** if it looks
  right. **Reject** undoes everything that post recorded, scores included.
  The bot doesn't tell the recorder.
- **🚩 Record report.** A member tapped **Report a mistake** on someone's
  profile and said what's wrong. **Approve** once you've dealt with it,
  **Reject** if nothing's wrong; neither changes the record by itself. A member
  can have three reports waiting at most, and nobody is told they were reported.

### Who is allowed to record

`submission_mode` has two settings:

| Mode | Who causes a write |
| --- | --- |
| `review` | Nobody. Every submission queues. |
| `trusted` *(default)* | Officers and people holding a **recorder role** — and never for themselves. |

There is deliberately no mode where a member's own post records anything. The
old `auto` mode is gone; a server that was on it starts up on `trusted`.

Set recorder roles in `/setup` or with `/config roles key:recorder_roles`. This is
how you scale past two people: give your raid leads the role, and audit after the
fact instead of gating everything beforehand. The Monday briefing is that audit.

**Nobody can record their own clear.** In every mode, the poster's own line in a
post goes to the queue while everyone else in the run is recorded. That hole
would otherwise be the easiest one to walk through.

**A recorder's post records straight away only when all of this is true:**

| Check | Otherwise |
| --- | --- |
| It has a screenshot attached | It waits: *no screenshot attached* |
| It hasn't been edited | Anyone an edit adds waits: *added by an edit* |
| The person isn't the poster | Their own line waits; everyone else is recorded |
| Its score isn't a new guild best for that trial | The score waits: *a new guild best* |

The reason is on the card. A score is only ever raised: anyone who already has a
better one keeps it.

**Every instant recording leaves evidence.** The bot posts its own copy of the
screenshot to `evidence_channel`, or the admin log if that isn't set, and every
change it made links to that copy: look for 📎 in `/audit`. The copy survives
the poster editing or deleting their post. If there's nowhere to keep it,
nothing records straight away. Changes you approve link to their card instead.

### What members can and cannot do

Nothing a member types changes a row. `/register`, `/submit score` and
`/submit parse` only ever create a request. Approval is the only write.

- `registration` — lets members raise a roster claim, either with `/register`
  or the **Claim my gamertag** button on their welcome card. On by default; a
  screenshot of their gamertag is required, and you still approve it.
- `member_submissions` — lets members send scores and parses with a screenshot.
  **Off** by default. Proof is mandatory when on.

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

### Still typing the old `!` commands?

Go ahead. The bot answers each old command with its new slash command, filled in
from what you typed:

```
!add_achievement [vcrgh][inkeddragonmami][healer]
→  /achievement give member:inkeddragonmami role:healer achievements:vcrgh
```

It **never runs** the old command — it only shows you the new one, which you send
yourself, so the officer check and the audit log still apply. Every command on the
old list is covered, `_g` versions included:

| Old | New |
| --- | --- |
| `!info` `!clears` | `/profile member:` |
| `!tank` `!healer` `!dps` | `/profile member: role:` |
| `!parse` | `/profile member:`, then 🏆 **Scores & parses** |
| `!add_achievement` | `/achievement give` |
| `!remove_achievement` | `/achievement take` |
| `!add_parse` | `/record parse` |
| `!update_name` | `/member link` |
| `!update_tag` | `/member rename` |
| `!cp` `!link` `!setup` `!craft` `!farming` | `/guide show` |
| `!export` | `/export` |
| `!command` | `/help` |

One answer per person every 20 seconds, so nobody can use it to make the bot spam.

### The roster

```
/member add     gamertag: NewGuy.  user: @newguy
/member link    gamertag: NewGuy.  user: @newguy
/member rename  gamertag: OldName.  new_gamertag: NewName.
/member merge   duplicate: NewGuy  keep: NewGuy.   ← two records, one person
/member remove  gamertag: Leaver.        ← keeps their record, hides them from lists
```

The bot never renames one member to make room for another. If a gamertag and a
Discord account disagree it says so and changes nothing.

**A trailing dot doesn't make a new person.** Most gamertags on the roster end
in a dot and members type theirs without it, so `/member add`, `/import` and
roster claims all treat `BUDMAN008` as `BUDMAN008.`. A claim names the record the
way the roster spells it and shows what it would inherit. If two records differ
only by the dot, the claim can't be approved until you merge them.

**`/member merge`** folds the duplicate into the record you keep: every clear
moves over, a regular clear replaces a legacy `L` mark, the better score per
trial and parse per label wins, and the Discord link moves if only the duplicate
had one. It shows you all of that first, then deletes the duplicate. It refuses
two records linked to different Discord accounts, and a duplicate that's still
in a request waiting on you. `/undo` puts every row back exactly as it was.

**Leaving and coming back.** Someone who leaves the server is taken off the
lists, and put back on when they rejoin, with their roles if `/sync` is on.
Someone you removed with `/member remove` stays removed.

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

**The bot also backs itself up.** Every day, and right before every `/import`,
it saves a full copy of the database to `data/backups` next to the database. It
keeps the newest 14 daily copies and 10 pre-import copies, and `/status` shows
when the last one was made. To restore one: stop the bot, copy the backup over
`data/unify.sqlite3`, and start it again. Each copy is the size of the database,
so allow room for about 24 of them.

---

## 10. Logging

Three channels, split by who reads them. Set them with `/config channel`.

| Setting | What lands there |
| --- | --- |
| `activity_channel` | Routine recordings, **batched into a digest** every `digest_minutes` (default 15) rather than one message each |
| `audit_channel` | Officer data changes, one per action, each with its `/undo` number |
| `problems_channel` | Only what needs a human: unrecognised roles, unknown members, failed commands, restarts |
| `evidence_channel` | The bot's own copy of the screenshot behind every instant recording. Empty means the admin log |

The problems channel should be empty on a good day. It's the only one you have to
watch.

### The morning briefing

Once a day at `briefing_hour` the bot posts what needs you: how many requests are
waiting and of what sort, how old the oldest is, how many achievements still have
no Discord role, how many roster entries aren't linked, and how much changed
yesterday. If you read one thing a day, read that.

**On Mondays it adds _Who recorded what this week_**: one line per recorder or
officer with how many achievements they recorded, for how many people, how many
were titles, and how many scores they recorded. Recorders write without a review, so this is your check on
them. A line gets a ⚠️ when someone recorded at least 10 achievements and 60% or
more went to the same three people. That isn't proof of anything, but it's worth
a look with `/audit actor:`.

### Retention

`audit_days` (default 365) prunes audit entries and decided requests older than
that, so exports don't get slower forever. Set it to `0` to keep everything.

---

## 11. When something goes wrong

```
/audit                          ← the last 40 changes
/audit member: BUDMAN008.       ← everything touching one person
/audit actor: @officer days: 7  ← what somebody changed this week
/audit action: grant            ← one sort of change
/undo            ← reverse the last one
/undo 42         ← reverse a specific entry from /audit
```

Every admin action writes its own inverse, so `/undo` is a real undo, not a guess.
Bulk imports are the exception, which is why one is backed up first. Entries with
a 📎 link to the screenshot they were recorded from.

Start with `/status`: it names most of these for you.

| Symptom | Fix |
| --- | --- |
| Slash commands don't appear | Check `GUILD_ID` in `.env`, restart. Without it they take up to an hour. |
| Bot ignores the achievements channel | `/status` — is `submissions_channel` set, and can the bot see and reply there? Is **Message Content Intent** on? |
| Every post says ❓ *Not on the roster* | Nobody is linked yet. Run `/member match`. |
| "I don't know what @X means yet" | `/map role` on that role, or press the button in the message. The post is read again. |
| "Not on the roster" for one person | `/member add` them, or `/member link` |
| Approve says nobody has a role yet | Pick Tank, Healer or DPS from the menu on the card, then Approve |
| A raid lead's post waited anyway | The card says why: no screenshot, an edited post, their own line, or a new guild best |
| A claim can't be approved: *matches 2 roster entries* | `/member merge` the two records, then approve it |
| An officer can't see the officer commands | Server Settings, then Integrations, then Unify: allow their officer role |
| `/member match` says it can only see itself | **Server Members Intent** is off. Turn it on, restart. |
| `/sync` skips roles | `/sync check` — the bot's role needs to sit above them |
| An achievement's name is wrong | `/achievement rename` |
| Wrong data got in | `/undo`, or `/audit` then `/undo 42` |

---

## Settings reference

`/config show` lists these with their current values, grouped by what they're
for, and flags the channels that must be set. Channels use `/config channel`,
role lists use `/config roles`, everything else `/config set`.

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
| `submission_mode` | `trusted` | `review` or `trusted` |
| `notify_dm` | `true` | DM members when they earn something |
| `mark_label` | `Legacy` | Name for the `L` marker |
| `role_sync` | `false` | Hand out Discord roles automatically |
| `recorder_roles` | — | May record for others, never for themselves |
| `registration` | `true` | Members may raise a roster claim |
| `member_submissions` | `false` | Members may send scores and parses with proof |
| `activity_channel` | — | Digest of routine recordings |
| `problems_channel` | — | Only things needing a human |
| `evidence_channel` | — | Copies of the screenshots behind instant recordings (empty = admin log) |
| `digest_minutes` | `15` | How often the activity digest posts |
| `briefing_hour` | `8` | Hour (UTC) of the morning briefing |
| `audit_days` | `365` | Audit retention, `0` for forever |
| `report_day` | `sun` | Weekly report day, or `off` |
| `report_hour` | `18` | Hour (0–23, UTC) |

---

## Checking the bot still works after a change

```bash
python tests/smoke.py
```

All checks run with no Discord connection needed. It builds the whole command tree and
exercises prerequisite chains, message parsing, spreadsheet import, role-sync
arithmetic and a full export → import round trip.
