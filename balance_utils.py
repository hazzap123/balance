"""
Shared utilities for Balance — time and usage management for Claude Code.

Used by both balance_hook.py (the hook) and balance-extend (the CLI).
"""

import fcntl
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ──
HOOKS_DIR = Path(__file__).parent
CONFIG_PATH = HOOKS_DIR / "balance.json"
USAGE_DIR = HOOKS_DIR / ".usage"

# ── Defaults ──
DEFAULT_CONFIG = {
    "enabled": True,
    "timezone": "Europe/London",
    "schedule": {
        "weekday": {
            "days": [1, 2, 3, 4, 5],
            "windows": [{"start": "08:00", "end": "18:00"}],
            "daily_limit_minutes": 240,
        },
    },
    "extensions": {
        "quick": {"minutes": 15, "max_per_day": 2, "label": "Quick 15-min session"},
        "more": {"minutes": 15, "max_per_day": 3, "label": "15 more minutes"},
    },
    "override": {"env_var": "BALANCE_OVERRIDE", "file": "~/.balance_override"},
    "warning_minutes_before_end": 15,
    "warning_minutes_before_cap": 30,
}


# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                uc = json.load(f)
            cfg = {**DEFAULT_CONFIG, **uc}
            cfg["schedule"] = uc.get("schedule", DEFAULT_CONFIG["schedule"])
            cfg["extensions"] = {**DEFAULT_CONFIG["extensions"], **uc.get("extensions", {})}
            cfg["override"] = {**DEFAULT_CONFIG["override"], **uc.get("override", {})}
            return cfg
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    return DEFAULT_CONFIG.copy()


# ═══════════════════════════════════════════════════════════════════
# Time helpers
# ═══════════════════════════════════════════════════════════════════

def get_now(timezone_name):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone_name))
    except (ImportError, KeyError):
        try:
            old_tz = os.environ.get("TZ")
            os.environ["TZ"] = timezone_name
            time.tzset()
            now = datetime.now()
            if old_tz is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = old_tz
            time.tzset()
            return now
        except Exception:
            return datetime.now()


def parse_time(t):
    """Parse 'HH:MM' to minutes since midnight. Raises ValueError on bad input."""
    try:
        parts = t.split(":")
        if len(parts) != 2:
            raise ValueError(f"expected HH:MM, got {t!r}")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"time out of range: {t!r}")
        return h * 60 + m
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid time {t!r}: {e}") from e


def fmt_minutes(m):
    """Format minutes since midnight as HH:MM."""
    return f"{m // 60:02d}:{m % 60:02d}"


def get_windows(sched):
    """Extract time windows from a schedule block.

    Supports two formats:
      New: {"windows": [{"start": "08:00", "end": "18:00"}, ...]}
      Legacy: {"start_hour": 8, "end_hour": 18, ...}
    Returns list of (start_minutes, end_minutes) tuples.
    """
    if "windows" in sched:
        return [(parse_time(w["start"]), parse_time(w["end"])) for w in sched["windows"]]

    # Legacy single-window format
    start = sched.get("start_hour", 0) * 60 + sched.get("start_minute", 0)
    end = sched.get("end_hour", 24) * 60 + sched.get("end_minute", 0)
    return [(start, end)]


def find_schedule(config, iso_weekday):
    """Find the schedule block covering this ISO weekday (1=Mon, 7=Sun)."""
    for name, sched in config["schedule"].items():
        if iso_weekday in sched.get("days", []):
            return name, sched
    return None, None


def in_any_window(windows, cur_m):
    """Check if current time (minutes) is inside any window.

    Returns (inside, window_start, window_end) where start/end are the
    matching window bounds, or (False, None, None).
    """
    for start_m, end_m in windows:
        if start_m <= cur_m < end_m:
            return True, start_m, end_m
    return False, None, None


def next_window_today(windows, cur_m):
    """Find the next window that starts after current time today."""
    upcoming = [(s, e) for s, e in windows if s > cur_m]
    if upcoming:
        upcoming.sort()
        return upcoming[0]
    return None


def next_available(config, now):
    """Find the next time Claude Code will be available."""
    cur_m = now.hour * 60 + now.minute

    # Check later today
    _, today_sched = find_schedule(config, now.isoweekday())
    if today_sched:
        nw = next_window_today(get_windows(today_sched), cur_m)
        if nw:
            return f"today at {fmt_minutes(nw[0])}"

    # Check upcoming days
    for offset in range(1, 8):
        future = now + timedelta(days=offset)
        _, sched = find_schedule(config, future.isoweekday())
        if sched:
            windows = get_windows(sched)
            if windows:
                earliest = min(w[0] for w in windows)
                return f"{future.strftime('%A')} at {fmt_minutes(earliest)}"
    return "unknown"


def windows_summary(windows):
    """Format windows as a readable string like '08:00-10:30 + 16:00-19:00'."""
    parts = [f"{fmt_minutes(s)}\u2013{fmt_minutes(e)}" for s, e in sorted(windows)]
    return " + ".join(parts)


# ═══════════════════════════════════════════════════════════════════
# Usage tracking — one file per day, one timestamp per prompt
# Active minutes = count of distinct clock-minutes with >= 1 prompt
# ═══════════════════════════════════════════════════════════════════

def usage_file_for(date_str):
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    return USAGE_DIR / f"{date_str}.log"


def record_prompt(now):
    date_str = now.strftime("%Y-%m-%d")
    path = usage_file_for(date_str)
    with open(path, "a") as f:
        f.write(f"{now.strftime('%H:%M')}\n")


def get_active_minutes(now):
    date_str = now.strftime("%Y-%m-%d")
    path = usage_file_for(date_str)
    if not path.exists():
        return 0
    seen = set()
    with open(path) as f:
        for line in f:
            seen.add(line.strip())
    return len(seen)


def cleanup_old_usage(now, keep_days=7):
    if not USAGE_DIR.exists():
        return
    # Compare dates only (avoids naive/aware datetime issues)
    cutoff_date = (now.replace(tzinfo=None) - timedelta(days=keep_days)).date()
    for p in USAGE_DIR.glob("*.log"):
        try:
            file_date = datetime.strptime(p.stem, "%Y-%m-%d").date()
            if file_date < cutoff_date:
                p.unlink()
        except (ValueError, OSError):
            pass
    # Also clean old extension logs
    for p in USAGE_DIR.glob("*.extensions.json"):
        try:
            date_part = p.stem.replace(".extensions", "")
            file_date = datetime.strptime(date_part, "%Y-%m-%d").date()
            if file_date < cutoff_date:
                p.unlink()
        except (ValueError, OSError):
            pass


def maybe_cleanup(now, keep_days=7):
    """Run cleanup at most once per day, tracked by a marker file."""
    if not USAGE_DIR.exists():
        return
    marker = USAGE_DIR / ".last_cleanup"
    today_str = now.strftime("%Y-%m-%d")
    if marker.exists():
        try:
            if marker.read_text().strip() == today_str:
                return
        except OSError:
            pass
    cleanup_old_usage(now, keep_days)
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(today_str)
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════
# Extensions
# ═══════════════════════════════════════════════════════════════════

def count_extensions_today(now, ext_type):
    date_str = now.strftime("%Y-%m-%d")
    log_path = USAGE_DIR / f"{date_str}.extensions.json"
    if not log_path.exists():
        return 0
    try:
        data = json.loads(log_path.read_text())
        return data.get(ext_type, 0)
    except (json.JSONDecodeError, OSError):
        return 0


def record_extension(now, ext_type):
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    log_path = USAGE_DIR / f"{date_str}.extensions.json"
    lock_path = log_path.with_suffix(".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        data = {}
        if log_path.exists():
            try:
                data = json.loads(log_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        data[ext_type] = data.get(ext_type, 0) + 1
        log_path.write_text(json.dumps(data))


# ═══════════════════════════════════════════════════════════════════
# Override checking
# ═══════════════════════════════════════════════════════════════════

def get_override_path(config):
    ov = config.get("override", {})
    return Path(os.path.expanduser(ov.get("file", "~/.balance_override")))


def check_override(config, now):
    """Full bypass override. Returns (active, info_str)."""
    ov = config.get("override", {})

    env_var = ov.get("env_var", "BALANCE_OVERRIDE")
    if os.environ.get(env_var, "").strip() in ("1", "true", "yes"):
        return True, "environment variable"

    ov_path = get_override_path(config)
    if ov_path.exists():
        try:
            data = json.loads(ov_path.read_text())
            expires_at = datetime.fromisoformat(data["expires_at"])
            now_naive = now.replace(tzinfo=None) if now.tzinfo else now
            expires_naive = expires_at.replace(tzinfo=None) if expires_at.tzinfo else expires_at

            if now_naive < expires_naive:
                remaining = (expires_naive - now_naive).total_seconds() / 60
                label = data.get("label", data.get("type", "override"))
                return True, f"{label} \u2014 {int(remaining)}m remaining"
            else:
                ov_path.unlink(missing_ok=True)
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            try:
                age_h = (time.time() - ov_path.stat().st_mtime) / 3600
                if age_h < 1:
                    return True, "override file (legacy format)"
                ov_path.unlink(missing_ok=True)
            except OSError:
                pass

    return False, ""
