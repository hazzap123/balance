#!/usr/bin/env python3
"""
Balance — time and usage restriction hook for Claude Code.

Enforces:
  1. Time windows — only allows interaction during configured hours
  2. Daily usage cap — tracks active minutes, blocks when limit hit

Supports multiple windows per day, extensions, and override bypass.

Hook event: UserPromptSubmit
Exit codes: 0 = allow, 2 = block (stderr shown to user)
"""

import json
import sys
from pathlib import Path

from balance_utils import (
    check_override,
    count_extensions_today,
    find_schedule,
    fmt_minutes,
    get_windows,
    in_any_window,
    load_config,
    get_now,
    maybe_cleanup,
    next_available,
    next_window_today,
    record_prompt,
    get_active_minutes,
    windows_summary,
)


# ═══════════════════════════════════════════════════════════════════
# Extension menu (shown on block)
# ═══════════════════════════════════════════════════════════════════

EXTEND_CMD = str(Path(__file__).parent / "balance-extend")


def extension_menu(config, now, context):
    """Build a block message with available extension options."""
    extensions = config.get("extensions", {})
    lines = []
    available = []
    for ext_type, ext in extensions.items():
        used = count_extensions_today(now, ext_type)
        max_d = ext["max_per_day"]
        remaining = max_d - used
        if remaining > 0:
            lines.append(f"  {EXTEND_CMD} {ext_type:<8} \u2014 {ext['label']} ({remaining} remaining)")
            available.append(ext_type)
        else:
            lines.append(f"  {EXTEND_CMD} {ext_type:<8} \u2014 {ext['label']} (none left)")

    if available:
        lines.append(f"  {EXTEND_CMD}          \u2014 interactive chooser")

    if not available:
        lines.append("\n  No extensions remaining. Take a break.")

    return "\n".join([context, "", "Run from terminal:"] + lines)


# ═══════════════════════════════════════════════════════════════════
# Core enforcement
# ═══════════════════════════════════════════════════════════════════

def check_window(config, now):
    """Check time-of-day window. Returns (in_window, sched_name, sched, active_end_m, block_msg)."""
    iso_day = now.isoweekday()
    sched_name, sched = find_schedule(config, iso_day)

    if sched is None:
        na = next_available(config, now)
        msg = extension_menu(config, now, f"Claude Code is offline today. Next available: {na}.")
        return False, None, None, None, msg

    windows = get_windows(sched)
    cur_m = now.hour * 60 + now.minute
    inside, _, end_m = in_any_window(windows, cur_m)

    if not inside:
        na = next_available(config, now)
        summary = windows_summary(windows)
        msg = extension_menu(
            config, now,
            f"Outside allowed hours ({summary}). Next window: {na}."
        )
        return False, sched_name, sched, None, msg

    return True, sched_name, sched, end_m, ""


def check_daily_cap(config, sched, now):
    """Check daily usage cap. Returns (under_cap, used_min, limit_min, block_msg)."""
    limit = sched.get("daily_limit_minutes")
    if limit is None:
        return True, 0, None, ""

    used = get_active_minutes(now)

    if used >= limit:
        msg = extension_menu(
            config, now,
            f"Daily limit reached ({used}/{limit} minutes used today)."
        )
        return False, used, limit, msg

    return True, used, limit, ""


def build_warnings(config, now, active_end_m, used_minutes, limit_minutes):
    """Build context warnings for approaching limits."""
    warnings = []
    cur_m = now.hour * 60 + now.minute

    # Window ending soon
    if active_end_m is not None:
        window_remaining = active_end_m - cur_m
        warn_window = config.get("warning_minutes_before_end", 15)
        if 0 < window_remaining <= warn_window:
            warnings.append(f"Window closes in {window_remaining} minutes.")

    # Daily cap approaching
    if limit_minutes is not None:
        remaining = limit_minutes - used_minutes
        warn_cap = config.get("warning_minutes_before_cap", 30)
        if 0 < remaining <= warn_cap:
            warnings.append(f"Daily usage: {used_minutes}/{limit_minutes} min ({remaining} min remaining).")

    return warnings


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    try:
        config = load_config()

        if not config.get("enabled", True):
            sys.exit(0)

        now = get_now(config.get("timezone", "Europe/London"))

        # Periodic cleanup (once per day, tracked by marker file)
        maybe_cleanup(now)

        # ── Override check (full bypass) ──
        override_active, override_info = check_override(config, now)
        if override_active:
            record_prompt(now)
            print(json.dumps({"additionalContext": f"Time override active: {override_info}"}))
            sys.stdout.flush()
            sys.exit(0)

        # ── Window check ──
        in_window, sched_name, sched, active_end_m, window_msg = check_window(config, now)
        if not in_window:
            print(window_msg, file=sys.stderr)
            sys.exit(2)

        # ── Daily cap check ──
        cap_ok, used, limit, cap_msg = check_daily_cap(config, sched, now)
        if not cap_ok:
            print(cap_msg, file=sys.stderr)
            sys.exit(2)

        # ── Allowed — record usage and check for warnings ──
        record_prompt(now)
        warnings = build_warnings(config, now, active_end_m, used, limit)
        if warnings:
            print(json.dumps({"additionalContext": " | ".join(warnings)}))
            sys.stdout.flush()

        sys.exit(0)

    except Exception as e:
        # Fail closed: unexpected errors block rather than allow
        print(f"Balance error (blocking): {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
