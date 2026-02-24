---
name: balance-configure
description: Interactively modify the Balance schedule — time windows, daily limits, timezone, and extensions
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
---

<objective>
Read the current balance.json, show the active config in plain English, then guide the user through changes and write them back.
</objective>

## Step 1 — Load current config

Read `~/.claude/hooks/balance.json`. If it doesn't exist, tell the user to run `/balance-setup` first.

## Step 2 — Display current config in plain English

Format the schedule clearly, for example:

```
Current Balance config:

Timezone: Europe/London

Schedule:
  Weekday (Mon–Fri): 08:00–18:00, 240 min/day cap
  Saturday: 08:00–10:30 + 16:00–19:00, 240 min/day cap
  Sunday: no access

Extensions:
  quick  — 15 min, max 2/day
  more   — 15 min, max 3/day

Warnings: 15 min before window end, 30 min before daily cap
```

## Step 3 — Ask what to change

Prompt the user: "What would you like to change?"

Common options to guide them:
- Add or remove a day/schedule block
- Change time windows (start/end hours)
- Adjust daily limit (minutes)
- Change timezone (IANA format, e.g. `America/New_York`)
- Add/remove/modify extension types
- Enable or disable Balance entirely

## Step 4 — Apply changes

Edit `~/.claude/hooks/balance.json` with the requested changes. Preserve the full JSON structure and any keys not being modified.

Validate:
- Times are in `HH:MM` format
- Days are ISO weekday integers (1=Mon, 7=Sun)
- Timezone is a valid IANA string
- No day appears in more than one schedule block

## Step 5 — Confirm

Show the updated section in plain English (same format as Step 2) and confirm: "Config saved. Changes take effect immediately — no restart needed."
