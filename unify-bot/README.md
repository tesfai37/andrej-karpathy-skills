# Unify Bot

A guild achievement tracker for Discord. Members look themselves up with menus;
officers run everything from slash commands. **Nothing is managed by editing
files** — after the one-time install, every day-to-day job happens inside Discord.

- Reads your achievement channel and records clears automatically
- Menu-driven profiles, leaderboards and stats
- Drag-and-drop spreadsheet import, one-click export
- Every change is logged and can be undone

---

## Part 1 — Install it once (about 10 minutes)

You only do this once, and only one person needs to.

### 1. Make the bot account

1. Go to <https://discord.com/developers/applications> → **New Application** → name it `Unify`.
2. Left menu → **Bot** → **Reset Token** → **Copy**. Keep that tab open.
3. Still on the Bot page, scroll to **Privileged Gateway Intents** and turn **on**:
   - **Server Members Intent**
   - **Message Content Intent**

   > Both are required. Without Message Content the bot cannot read your
   > achievement channel; without Server Members it cannot see who is who.

### 2. Invite it to your server

Left menu → **OAuth2** → **URL Generator**:

- **Scopes:** `bot`, `applications.commands`
- **Bot permissions:** View Channels, Send Messages, Send Messages in Threads,
  Embed Links, Attach Files, Read Message History, Add Reactions, Use External Emojis,
  **Manage Roles**

  > Manage Roles is only needed for `/sync` (handing out earned roles). Everything
  > else works without it. Discord also requires the bot's own role to sit **above**
  > any role it hands out — `/sync check` tells you if it doesn't.

Copy the URL at the bottom, open it, pick your server, **Authorise**.

### 3. Start it

```bash
cd unify-bot
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` in any text editor and fill in two lines:

| Line | What to put there |
| --- | --- |
| `DISCORD_TOKEN=` | the token you copied in step 1 |
| `GUILD_ID=` | your server's ID — right-click the server icon → **Copy Server ID**<br>(if you don't see that, turn on Discord → Settings → Advanced → **Developer Mode**) |

Then:

```bash
python main.py
```

You should see `logged in as Unify#1234`. Leave it running.

> **Prefer Docker?** `docker compose up -d` does the same thing, and restarts the
> bot automatically if the machine reboots.

### 4. Run `/setup` in Discord

Type `/setup` in any channel. One screen, four things, all dropdowns:

1. **Submissions channel** — where people post their clears
2. **Admin log channel** — where the bot writes what changed
3. **Admin roles** — who is allowed to change data
4. **Auto-map roles** — press it and the bot matches your existing Discord roles
   to achievements by name

That's the install finished.

---

## Part 2 — Teaching the bot your roles

This is the part that makes the automatic channel reading work, so it's worth
five minutes.

**The idea:** your server already has roles like `vSS`, `vSS HM`, `Godslayer`.
The bot needs to know that the role `Godslayer` means the achievement
`vssgodslayer`. That link is called a **mapping**.

### The fast way

```
/map auto
```

The bot reads every role in your server, matches the ones whose names it
recognises, and shows you the list **before** saving anything. Read it, press
**Save all**. Wrong guesses can be fixed one at a time afterwards.

### The careful way — one role at a time, no typing

```
/map role  role: @vSS Ice HM
```

You get two dropdowns:

1. **Which trial?** → pick *Sunspire*
2. **Which achievement?** → pick *Lokkestiiz HM*

Done. Repeat for anything `/map auto` missed.

### Check your work

```
/map missing
```

Lists every achievement that still has no Discord role, plus a warning if
Tank / Healer / DPS roles aren't mapped yet.

```
/map list
```

Shows every mapping you have.

---

## Part 3 — How members post achievements

Once mapping is done, people post in the submissions channel exactly the way
they already do:

```
@BUDMAN008 dps @vSS Ice HM @vSS Fire HM
```

The bot reads it and works out:

| It sees | It understands |
| --- | --- |
| `@BUDMAN008` | which member |
| `dps` (typed, or a mapped @DPS role) | which role |
| `@vSS Ice HM` `@vSS Fire HM` | `vssice`, `vssfire` |

…then records `vss`, `vssice` and `vssfire` — because both hard modes require
the base clear, and the bot fills prerequisites in by itself.

**Two modes**, set with `/config set key:submission_mode`:

- `review` *(default)* — the bot posts an **Approve / Reject** card under the
  message and waits for an officer
- `auto` — recorded straight away, with a ✅ reaction

**Nothing is ever dropped silently.** If the bot doesn't recognise something it
says so in the channel:

- ❓ *"I don't know what @Some Role means yet"* → a **Map these roles** button
  that opens the same two-dropdown picker
- ❓ *"I couldn't tell who this is for"* → the poster @mentions them or types
  their exact gamertag

---

## Part 4 — Bringing your old data across

### From the old `database.db`

```bash
python tools/migrate_legacy.py /path/to/old/database.db
```

Reads the old `TANK` / `HEALER` / `DPS` / `AWA` / `SCORE` / `PARSE` tables, tells
you exactly what it found, and never touches the old file. Any column it doesn't
recognise is listed at the end so you can decide what to do with it.

### From a spreadsheet, inside Discord

```
/import   ← then drag your .xlsx or .csv onto Discord
```

The bot reads the file, tells you what it found (*"Tank — 87 people, 53
achievement columns → Tank"*), lists anything it will ignore, and waits for you
to press **Import**.

It already understands the layout you use: a sheet per role, a `GamerTag`
column, a `DiscordName` column, and `X` in an achievement column. `Score` and
`Parse` sheets are picked up too — `112k`, `98,400` and `1.2m` all parse
correctly.

> A bulk import is **not** covered by `/undo`. Run `/export` first if you want a
> restore point.

---

## Part 5 — Everyday use

### For members

| Command | What it does |
| --- | --- |
| `/me` | Your own achievement card |
| `/profile` | Look somebody up — start typing, the list narrows as you go |
| `/find` | Who has (and hasn't) cleared what — the group-building question |
| `/leaderboard` | Points, achievements, scores or parses |
| `/stats` | How the guild is doing overall |
| `/score` · `/parse` | Somebody's numbers |
| `/roster` | Everybody, paginated |
| `/help` | A menu, not a wall of text |

The profile card opens on a summary: a progress bar per trial you've actually
touched, everything untouched collapsed into one line, and your titles. Two
menus underneath switch role or open a single trial for the boss-by-boss list.

### For officers

| Command | What it does |
| --- | --- |
| `/member add` · `link` · `rename` · `remove` | Roster |
| `/achievement give` · `take` | Grant or remove — prerequisites handled |
| `/record score` · `/record parse` | Numbers |
| `/trial add` · `/achievement new` · `/achievement rename` | New content, no code |
| `/import` · `/export` | Spreadsheets in and out |
| `/audit` | Every change, who made it, when |
| `/undo` | Reverse the last change (or `/undo 42` for a specific one) |
| `/sync check` · `all` · `member` | Hand out the Discord roles people have earned |
| `/config show` · `set` · `channel` · `roles` | Settings |

---

## Building a group — `/find`

The question every raid lead asks on a Tuesday:

```
/find  has: vsshm  missing: vssgodslayer  role: DPS
```

> *"Every DPS who has cleared Sunspire hard mode but hasn't got Godslayer yet."*

- **has** — codes they must all have. Pick from the suggestions as you type; each
  pick is added to the line rather than replacing it.
- **missing** — codes they must *not* have yet
- **role** — only count clears done as Tank / Healer / DPS
- **min_parse** / **parse_label** — only people parsing above a number

Results lead with the most experienced, and a **Copy as @mentions** button gives
you the list as plain text you can paste into a ping. (It comes back in a code
block, so nobody gets pinged by accident.)

---

## Giving out roles automatically — `/sync`

Your Discord roles already say who has cleared what. Once mapping is done, the
bot can keep them that way by itself: earn Godslayer, get the Godslayer role.

It is **off** until you turn it on, because the first run can hand out a lot of
roles at once:

```
/sync check     ← always start here
/config set  key:role_sync  value:true
/sync all       ← shows a preview and waits for you to confirm
```

`/sync check` tells you the two things that actually go wrong:

1. Whether the bot has the **Manage Roles** permission
2. Which achievement roles sit **above** the bot in Server Settings → Roles

Discord will not let any bot hand out a role above its own. If `/sync check`
lists roles as *out of reach*, drag the bot's role above them and run it again.

After that, roles update by themselves whenever an achievement is recorded — from
the submissions channel, from `/achievement give`, and they come back off if an
achievement is removed. A bulk `/import` doesn't sync inline (that would be
thousands of role changes at once); it reminds you to run `/sync all` instead.

**Only roles that appear in `/map list` are ever touched.** Anything else a
member has — raid roles, colour roles, pings — the bot leaves completely alone.

---

## How the achievement chain works

Each achievement can require others. Grant the top one and everything below it
comes along:

```
vssgodslayer  →  vsshm  →  vssice + vssfire + vssnavi  →  vss
```

Removing works the other way: take away `vss` and everything that depended on it
goes too, so a record can never end up claiming a hard mode without the clear.

Points on the leaderboard follow the same idea — clear `1`, boss HM `3`,
full HM `5`, title `10`.

**New trial released?** No code, no restart:

```
/trial add        key:vox  short:vOX  name:Ossein Cage  emoji:🦴
/achievement new  trial:vOX  key:vox  name:Veteran Ossein Cage  kind:Trial clear
/achievement new  trial:vOX  key:voxhm name:Ossein Cage HM      kind:Full hard mode  requires:vox
/map role         role:@vOX HM
```

---

## Settings reference

`/config show` lists all of these with their current values.

| Key | What it controls |
| --- | --- |
| `guild_name`, `brand_color`, `logo_url` | Branding on every embed |
| `submissions_channel` | Channel the bot watches |
| `audit_channel` | Where changes are logged |
| `welcome_channel` | Welcome card when someone joins |
| `reports_channel` | Weekly report + backup |
| `admin_roles` | Who can change data |
| `viewer_roles` | Who can look people up (empty = everyone) |
| `submission_mode` | `auto` or `review` |
| `notify_dm` | DM members when they earn something |
| `role_sync` | Hand out Discord roles automatically when achievements are earned |
| `report_day`, `report_hour` | Weekly report schedule (UTC, `off` to disable) |

---

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| Slash commands don't appear | Check `GUILD_ID` in `.env`, then restart the bot. Without it commands take up to an hour to show up. |
| The bot ignores the submissions channel | `/config show` — is `submissions_channel` set? Is **Message Content Intent** on in the developer portal? |
| "I don't know what @X means yet" | `/map role` on that role, or press the button in the message. |
| "I couldn't tell who this is for" | `/member add` them, or `/member link` their gamertag to their Discord account. |
| Wrong data got in | `/undo`. For something older, `/audit` to find the number, then `/undo 42`. |
| An achievement's name is wrong | `/achievement rename` — the seed names are a starting point, not gospel. |
| *"that Discord account is already X"* | Two gamertags are pointing at one Discord account. Nothing was overwritten — decide which is right, then `/member rename` or `/member link`. |
| An import skipped a column | It's listed in the preview under *Columns I'll ignore*. Add it with `/achievement new`, then import again. |

---

## Checking it still works after a change

```bash
python tests/smoke.py
```

Builds the whole command tree and exercises prerequisite chains, message
parsing and spreadsheet import offline — no Discord connection needed.

## Layout

```
main.py                  start here
unify/
  bot.py                 bot object, audit relay, error handling
  db.py                  schema + query helpers
  store.py               members, grants, scores, audit/undo
  parsing.py             reads an achievement post
  importer.py            reads a spreadsheet
  rolesync.py            works out which Discord roles somebody should hold
  embeds.py  views.py    everything you see
  cogs/                  one file per area
  data/catalog.json      seed trials and achievements (edited in Discord after first run)
tools/migrate_legacy.py  old database.db -> new schema
tests/smoke.py           offline checks
```
