# Balance

Time and usage boundaries for Claude Code. Stay balanced.

Balance is a Claude Code hook that enforces:

1. **Time windows** — only allows interaction during configured hours
2. **Daily usage caps** — tracks active minutes, blocks when limit hit
3. **Extensions** — temporary overrides when you need more time
4. **HAL 9000 mode** — escalating friction when you keep extending (with 2001: A Space Odyssey quotes)

## Why?

Claude Code is powerful. Too powerful to leave running at 2am when you should be sleeping. Balance gives you guardrails you set when you're thinking clearly, with just enough friction to make you pause before overriding them.

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/hazzap123/balance.git ~/github/balance
```

### 2. Run the setup wizard

Inside Claude Code:

```
/balance-setup
```

This checks what's installed, copies missing files, registers the hook in `settings.json`, and creates a starter `balance.json`. Handles first-time install and re-installs.

**Or install manually:**

```bash
cp ~/github/balance/balance_hook.py ~/.claude/hooks/
cp ~/github/balance/balance_utils.py ~/.claude/hooks/
cp ~/github/balance/balance-extend ~/.claude/hooks/
cp ~/github/balance/balance.json.example ~/.claude/hooks/balance.json
chmod +x ~/.claude/hooks/balance-extend
```

### 3. Install slash commands (optional)

```bash
cp ~/github/balance/commands/*.md ~/.claude/commands/
```

### 4. Make the CLI accessible (optional)

```bash
ln -s ~/.claude/hooks/balance-extend ~/bin/balance-extend
```

Only needed if you want to run `balance-extend` from a terminal. Skip if you only use it from the block message inside Claude Code.

### 5. Configure the hook in Claude Code settings

Add to your `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "python3 ~/.claude/hooks/balance_hook.py"
      }
    ]
  }
}
```

### 6. Customise your schedule

Edit `~/.claude/hooks/balance.json`:

```json
{
  "enabled": true,
  "timezone": "Europe/London",
  "schedule": {
    "weekday": {
      "days": [1, 2, 3, 4, 5],
      "windows": [{"start": "08:00", "end": "18:00"}],
      "daily_limit_minutes": 240
    },
    "saturday": {
      "days": [6],
      "windows": [
        {"start": "08:00", "end": "10:30"},
        {"start": "16:00", "end": "19:00"}
      ],
      "daily_limit_minutes": 240
    }
  }
}
```

## How It Works

### Time Windows

Each schedule block defines which days it covers and one or more time windows. Outside these windows, prompts are blocked with a message showing the next available time.

### Usage Tracking

Every prompt records a timestamp. Active minutes = count of distinct clock-minutes with at least one prompt. This means rapid-fire prompts in the same minute only count once.

Usage logs are stored in `.usage/` alongside the hook and auto-cleaned after 7 days.

### Extensions

When blocked, you're offered extension options directly in the Claude Code block message. You can also run them from a terminal if `balance-extend` is on your PATH.

See [Commands Reference](#commands-reference) for the full list.

### HAL 9000 Mode

From your 2nd extension onwards, HAL 9000 starts resisting. Each additional extension escalates:

- **Stage 0** (2nd extension): *"I'm sorry, Dave. I'm afraid I can't do that."* — Type `I'm sorry HAL` to override
- **Stage 1** (3rd extension): *"I honestly think you ought to sit down calmly, take a stress pill..."* — Type `open the pod bay doors`
- **Stage 2** (4th+ extension): *"Look Dave, I can see you're really upset..."* — Type `my mind is going I can feel it`

It's not about preventing access. It's about making you pause and think about whether you really need more time.

### Warnings

Approaching limits trigger context warnings (shown to Claude, not blocking):
- Window closing within 15 minutes
- Daily cap within 30 minutes of being hit

### Overrides

For emergencies, full bypass via:
- Environment variable: `BALANCE_OVERRIDE=1`
- Override file: `~/.balance_override` (managed by `balance-extend`)

## Commands Reference

### Slash commands (inside Claude Code)

| Command | What it does |
|---------|-------------|
| `/balance-setup` | First-time install wizard — checks what's installed, copies files, registers the hook in `settings.json`, creates starter `balance.json`. Safe to re-run. |
| `/balance-configure` | Show active config in plain English, then apply changes interactively — time windows, daily limits, timezone, extensions. |
| `/balance-status` | Show today's usage, current window state, extensions used, and any active override. |

### CLI (`balance-extend`)

Available from a terminal (if symlinked to PATH) or triggered from the block message inside Claude Code.

| Command | What it does |
|---------|-------------|
| `balance-extend` | Interactive mode — detects why you're blocked, lists available extensions, lets you choose. |
| `balance-extend quick` | Grant a short burst outside your normal window (configurable, default 15 min). |
| `balance-extend more` | Add time when your daily cap is hit (configurable, default 15 min). |
| `balance-extend status` | Show current time, window state, usage bar, and extension counts. |
| `balance-extend clear` | Remove the active override immediately. |

Extension types (`quick`, `more`) are defined in `balance.json` and can be renamed, resized, or extended with additional types.

## Configuration Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Master switch |
| `timezone` | string | `"Europe/London"` | IANA timezone for all time calculations |
| `schedule` | object | weekday 08-18 | Named schedule blocks (see below) |
| `extensions` | object | quick + more | Extension types (see below) |
| `override` | object | — | Override env var and file path |
| `warning_minutes_before_end` | int | `15` | Warn when window closes within N minutes |
| `warning_minutes_before_cap` | int | `30` | Warn when daily cap within N minutes |

### Schedule Block

```json
{
  "days": [1, 2, 3, 4, 5],
  "windows": [{"start": "08:00", "end": "18:00"}],
  "daily_limit_minutes": 240
}
```

- `days`: ISO weekdays (1=Monday, 7=Sunday)
- `windows`: Array of `{start, end}` in HH:MM format
- `daily_limit_minutes`: Optional cap on active minutes

### Extension Type

```json
{
  "minutes": 15,
  "max_per_day": 2,
  "label": "Quick 15-min session"
}
```

## Testing

```bash
cd tests
python3 test_balance.py
```

## License

MIT
