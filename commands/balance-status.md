---
name: balance-status
description: Show current Balance usage, active schedule, extensions used today, and any active overrides
allowed-tools:
  - Read
  - Bash
  - Glob
---

<objective>
Read today's usage logs and config, then display a clear status summary — minutes used, schedule, extensions, overrides.
</objective>

## Step 1 — Load config

Read `~/.claude/hooks/balance.json`. If missing, report "Balance not configured — run `/balance-setup`."

Check if `enabled` is false — if so, report "Balance is disabled."

## Step 2 — Get today's date and timezone

Run: `python3 -c "from zoneinfo import ZoneInfo; from datetime import datetime; tz='<TIMEZONE>'; now=datetime.now(ZoneInfo(tz)); print(now.strftime('%Y-%m-%d %H:%M'), now.isoweekday())"`

Replace `<TIMEZONE>` with the value from config (default: `Europe/London`).

## Step 3 — Read today's usage

Read `~/.claude/hooks/.usage/YYYY-MM-DD.log` (today's date). Count distinct HH:MM lines = active minutes used.

If file doesn't exist: 0 minutes used today.

## Step 4 — Read today's extensions

Read `~/.claude/hooks/.usage/YYYY-MM-DD.extensions.json`. Shows how many of each extension type have been used today.

## Step 5 — Check for active override

Check if `~/.balance_override` exists and read its `expires_at` field. Calculate remaining minutes.

Also check if `BALANCE_OVERRIDE` env var is set.

## Step 6 — Find today's schedule

Match today's ISO weekday against the schedule blocks in config. If no match: today is a blocked day.

## Step 7 — Display status

Format as:

```
Balance Status — Tuesday 08 Apr, 12:34 (Europe/London)

Schedule:   Weekday — 08:00–18:00
Window:     OPEN (closes in 5h 26m)
Usage:      47 / 240 min today
Extensions: quick ×1 used (1 remaining) | more ×0 used (3 remaining)
Override:   none active
```

Adjust based on actual state:
- If outside window: show "CLOSED — next window: [time]"
- If daily cap hit: show "CAP REACHED"
- If override active: show expiry time and remaining minutes
- If blocked day: show "No access today — next: [day]"
