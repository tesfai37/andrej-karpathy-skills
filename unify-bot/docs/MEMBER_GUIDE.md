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

**Three rules that make it work every time:**

1. **@mention the person.** Their nickname typed as text is a guess; a mention is
   certain. If they aren't on the roster yet the bot says so instead of guessing.
2. **Say the role** — `tank`, `healer` or `dps`, typed or pinged. Leave it out and
   the bot posts a little menu asking which one.
3. **Ping the achievement roles**, don't type their names.

Depending on how your officers set it up, the bot either records it straight away
(✅ reaction) or posts an **Approve / Reject** card for an officer (⏳ reaction).

**If something goes wrong it tells you** — it never silently drops a post:

- ❓ *"I don't know what @Some Role means yet"* — an officer taps a button to teach it
- ❓ *"I couldn't tell who this is for"* — @mention them, or ask an officer to add them

You'll get a DM when something lands on your record. Officers can turn that off
server-wide, but you can also just mute the bot.

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

Start typing a gamertag and the list narrows as you go — pick from the
suggestions. Or use the `user:` option and pick them from the server instead.

| Command | What you get |
| --- | --- |
| `/profile` | Their achievement card, with the same menus |
| `/score` | Their trial scores, and the total |
| `/parse` | Their parse numbers, best first |
| `/roster` | Everybody in the guild, paginated |

`/roster search: bud` filters the list.

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
/guide list
```

One search box covers every category — start typing `magblade`, `tank`, `vss`,
whatever you're after. `/guide list` shows everything grouped by category.

---

## Quick reference

| I want to… | Command |
| --- | --- |
| See my own card | `/me` |
| Look somebody up | `/profile` |
| See their scores or parses | `/score` · `/parse` |
| Find people for a run | `/find` |
| See who's on top | `/leaderboard` |
| See how the guild is doing | `/stats` |
| Look up a CP build or link list | `/guide show` |
| See everyone | `/roster` |
| Work out what else it does | `/help` |
