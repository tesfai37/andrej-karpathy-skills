# Unify Bot — Member Guide

Everything you can do without being an officer. All of it works on your phone.

**Lost?** Type `/help` in any channel. It's a menu, not a wall of text.

---

## Getting your clears recorded

Post in the achievements channel the way you always have — mention the person,
say the role, and ping the achievement roles:

```
@BUDMAN008 dps @vSS Ice HM @vSS Fire HM
```

The bot reads it and works out three things:

| It sees | It understands |
| --- | --- |
| `@BUDMAN008` | who it's for |
| `dps` (typed, or a mapped @DPS role) | which role you cleared as |
| `@vSS Ice HM` `@vSS Fire HM` | the two hard modes |

It then records **Sunspire, Lokkestiiz HM and Yolnahkriin HM** — the base clear
comes along automatically, because you can't have a hard mode without it.

### A whole run in one post

Raid leads can post the full group at once instead of twelve separate posts:

```
@vSS HM @Godslayer
vss score 257,498
tank: @a @b
healer: @c @d
dps: @e @f @g @h @i @j @k @l
```

- A role on a line counts for **everyone mentioned on that line**.
- A role on a line of its own counts for the lines below it, until the next role.
- If the whole post names only one role, everybody gets it.
- A line with the word **score** gives the run's score to everyone in the run.
  Name the trial on that line, or the bot works it out from the achievements.
- Two roles on one line, or one person under two roles, is left for an officer
  to sort out. The bot never guesses.

**Four rules that make it work every time:**

1. **@mention the person.** Their nickname typed as text is a guess; a mention is
   certain. If they aren't on the roster yet the bot says so instead of guessing.
2. **Say the role** — `tank`, `healer` or `dps`, typed or pinged. Leave it out and
   the post waits for an officer to pick one.
3. **Ping the achievement roles**, don't type their names.
4. **Attach the screenshot.** It's copied onto the card the officer checks, and
   without one nothing records straight away, even from a raid lead.

### Who records it

| Who posted | What happens |
| --- | --- |
| A raid lead or officer, for other people, with a screenshot | Recorded straight away ✅ |
| Anyone, for **their own** clear | Waits for an officer ⏳, raid leads included |
| Anyone, without a screenshot | Waits for an officer ⏳ |
| Everybody else | Waits for an officer ⏳ |

If your officers have set the bot to review everything, every post waits.
Nobody can record their own clear. A score never goes down: if someone already
has a better one for that trial, they keep it. A score that would be the
guild's best ever for that trial waits for an officer, so a typo can't top the
leaderboard.

The bot answers once, with what happened to everyone in the post:

| You see | It means |
| --- | --- |
| ✅ Recorded for 11 | Done |
| ⏳ Waiting for an officer | An officer approves it from a card under your post |
| 🏆 vss score 257,498 for 11 | The run's score went on everyone who didn't have better |
| ♻️ Already had it | Nothing new to record |
| ❓ Not on the roster | That person needs `/register`, or an officer to add them |
| ❓ I don't know what @Some Role means yet | An officer teaches it, and your post is read again |

Made a mistake? **Edit the post** and the bot reads it again. An edit can add
people, but anyone it adds waits for an officer, and it can't take back anything
that's already been recorded.

You'll get a DM when something lands on your record. Officers can turn that off
server-wide, but you can also just mute the bot.

---

## Getting on the roster

Tap **Claim my gamertag** on your welcome card, or type:

```
/register  gamertag: YourName.  proof: <screenshot>
```

Type it with or without the dot at the end; the bot finds your record either
way. Either way you send a **screenshot of your gamertag in game**. This only *asks*
an officer to link your gamertag to your Discord account. Nothing is linked until
an officer has looked at the screenshot and approved it, and if the gamertag
already has a record they check it carefully before handing it over.

```
/me
```

Your card, plus anything of yours still waiting on an officer: a claim, a score,
or a run you were in. Before you're linked it tells you where you stand instead.
The waiting list is only shown to you.

Leave the server and come back later? Your record comes back with you.

### Sending in a score or a parse

If your officers have turned this on:

```
/submit score  trial: vss  score: 257498  proof: <screenshot>
/submit parse  label: Arcanist  dps: 118000  proof: <screenshot>
```

The screenshot is **required**, and nothing is recorded until an officer looks at
it and approves. If the command tells you submissions are off, pass your
screenshot to an officer instead.

---

## Looking yourself up

```
/me
```

Your card opens on DPS and looks like this:

```
⚔️  BUDMAN008 — DPS
▰▰▰▰▰▱▱▱▱▱▱▱  21/59 achievements
@budman008 • ⭐ 87 pts • 🏆 412,000 total score

Progress
🐉 vSS   ▰▰▰▰▰▱ 5/6
⚡ vKA   ▰▰▱▱▱▱ 2/6
🌋 vRG   ▰▱▱▱▱▱ 1/6

Not started
vAS, vCR, vHoF, vMoL, vBRP, vDSR, vSE, vLC, vOC

🏅 Titles   Godslayer • Unchained
```

Only trials you've actually touched get a bar. Everything else collapses into one
line, so the card stays short on a phone.

**Two menus underneath:**

- **Change role** — switch between Tank, Healer, DPS and Account Wide
- **Open a trial** — the full boss-by-boss breakdown for one trial

The breakdown uses three marks:

| Mark | Meaning |
| --- | --- |
| ✅ | You have it |
| 🅛 | **Legacy** — earned before ESO made achievements account-wide. Still counts everywhere. |
| ⬜ | Not yet |

The menus belong to whoever ran the command. If someone else's card is on screen,
run `/me` or `/profile` yourself to get one you can drive.

---

## Looking other people up

```
/profile member: bud
```

Start typing a gamertag **or their Discord name** and the list narrows as you go —
pick from the suggestions. Or use the `user:` option and pick them from the
server instead. If a name could be more than one person, the bot lists who it
could be rather than guessing.

| Command | What you get |
| --- | --- |
| `/profile` | Their achievement card, with the same menus |
| 🏆 **Scores & parses** on the card | Their trial scores with the total, and their parses, best first |
| 🚩 **Report a mistake** on the card | Tell the officers something on this record is wrong |
| `/roster` | Everybody in the guild, paginated |

`/roster search: bud` filters the list.

### Something on a record is wrong?

Tap 🚩 **Report a mistake** on the card and say what's wrong, like *"They
weren't in Friday's vSS HM run"*. An officer looks at it. Nothing changes unless
they decide it should, and the person isn't told they were reported. You can
have three reports waiting at a time.

---

## Building a group — `/find`

The one worth learning. It answers "who can I bring?"

```
/find  has: extinguisher  missing: godslayer  role: DPS
```

> *Every DPS who has cleared Sunspire hard mode but hasn't got Godslayer yet.*

| Option | What it does |
| --- | --- |
| `has` | Codes they must **all** have |
| `missing` | Codes they must **not** have yet |
| `role` | Only count clears done as Tank / Healer / DPS |
| `min_parse` | Only people parsing at least this |
| `parse_label` | …on one specific parse, e.g. `Arcanist` |
| `marked` | Only normal clears, or only legacy records |

**Picking several codes:** the suggestions add to your line instead of replacing
it, so tapping three in a row builds `extinguisher vkahm vrghm`.

Results lead with the most experienced people. A **Copy as @mentions** button
gives you the whole list as text you can paste into a ping — it comes back in a
code block, so nobody gets pinged by accident.

---

## Leaderboards and guild stats

```
/leaderboard
```

Four boards, picked with the `board:` option:

| Board | Ranks by |
| --- | --- |
| **Points** (default) | Weighted score — clear 1, boss hard mode 3, full hard mode 5, title 10 |
| **Most achievements** | Raw count. Add `role:` for one role only |
| **Trial score** | Total across every trial, or one trial with `trial:` |
| **Parse** | Everyone's best, or one target with `parse_target:` |

```
/stats
```

How the guild is doing overall — how many members, how many achievements
recorded, and how many people have cleared each trial.

---

## Guild reference — `/guide`

The CP builds, channel directories and setups the officers maintain.

```
/guide show  topic: cp · 2100dps
/guide show
```

One search box covers every category — start typing `magblade`, `tank`, `vss`,
whatever you're after. Leave the topic blank to see everything, grouped by
category.

---

## Still typing the old `!` commands?

That's fine. Type one and the bot answers with the new command, filled in:

```
!info budman008     →   /profile member:budman008
!cp magblade        →   /guide show topic:cp/magblade
```

It never runs the old command. It only shows you the new one to send.

---

## Quick reference

| I want to… | Command |
| --- | --- |
| See my own card | `/me` |
| Look somebody up | `/profile` |
| See their scores or parses | `/profile`, then 🏆 **Scores & parses** |
| Find people for a run | `/find` |
| See who's on top | `/leaderboard` |
| See how the guild is doing | `/stats` |
| Look up a CP build or link list | `/guide show` |
| See everyone | `/roster` |
| Get on the roster | **Claim my gamertag** on your welcome card, or `/register` |
| Check my link and what's pending | `/me` |
| Report something wrong on a record | 🚩 **Report a mistake** on the profile card |
| Send in a score or parse | `/submit score` · `/submit parse` |
| Work out what else it does | `/help` |
