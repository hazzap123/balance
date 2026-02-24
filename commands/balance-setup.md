---
name: balance-setup
description: First-time setup wizard for the Balance Claude Code hook — installs files, configures settings.json, and creates balance.json
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
---

<objective>
Walk through first-time Balance installation. Check what's already installed, copy missing files, register the hook in settings.json, and create a starter config.
</objective>

## Step 1 — Check what's already installed

Run: `ls ~/.claude/hooks/balance_hook.py ~/.claude/hooks/balance_utils.py ~/.claude/hooks/balance-extend ~/.claude/hooks/balance.json 2>&1`

Note which files exist and which are missing.

## Step 2 — Check if hook is registered

Read `~/.claude/settings.json`. Look for an entry in `hooks.UserPromptSubmit` that references `balance_hook.py`.

## Step 3 — Install missing files

If any of `balance_hook.py`, `balance_utils.py`, or `balance-extend` are missing, ask the user to clone the repo first:

```bash
git clone https://github.com/hazzap123/balance.git ~/github/balance
```

Then copy missing files:
```bash
cp ~/github/balance/balance_hook.py ~/.claude/hooks/
cp ~/github/balance/balance_utils.py ~/.claude/hooks/
cp ~/github/balance/balance-extend ~/.claude/hooks/
chmod +x ~/.claude/hooks/balance-extend
```

## Step 4 — Register hook in settings.json

If not already registered, add to the `hooks.UserPromptSubmit` array in `~/.claude/settings.json`:

```json
{
  "type": "command",
  "command": "python3 ~/.claude/hooks/balance_hook.py"
}
```

If `hooks` or `UserPromptSubmit` keys don't exist, create them. Preserve all existing settings.

## Step 5 — Create balance.json if missing

If `~/.claude/hooks/balance.json` doesn't exist, copy the example:
```bash
cp ~/github/balance/balance.json.example ~/.claude/hooks/balance.json
```

Then tell the user: "Balance is installed. Run `/balance-configure` to set your schedule, or `/balance-status` to verify it's working."

## Step 6 — Optional: symlink CLI

Ask if they want `balance-extend` on their PATH:
```bash
ln -s ~/.claude/hooks/balance-extend ~/bin/balance-extend
```

Only suggest this if `~/bin` exists on their PATH.

## Done

Confirm what was installed and what was already present. Keep the summary concise.
