"""
Tests for Balance.

Run from repo root: python3 tests/test_balance.py
Or: cd tests && python3 test_balance.py
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase, main as unittest_main
from unittest.mock import patch

# Ensure the repo root (where balance_hook.py and balance_utils.py live) is on the path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import balance_utils


# ═══════════════════════════════════════════════════════════════════
# Test fixtures
# ═══════════════════════════════════════════════════════════════════

SAMPLE_CONFIG = {
    "enabled": True,
    "timezone": "Europe/London",
    "schedule": {
        "weekday": {
            "days": [1, 2, 3, 4, 5],
            "windows": [{"start": "08:00", "end": "18:00"}],
            "daily_limit_minutes": 240,
        },
        "saturday": {
            "days": [6],
            "windows": [
                {"start": "08:00", "end": "10:30"},
                {"start": "16:00", "end": "19:00"},
            ],
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


def make_dt(year=2026, month=2, day=24, hour=10, minute=0):
    """Create a naive datetime. 2026-02-24 is a Tuesday (isoweekday=2)."""
    return datetime(year, month, day, hour, minute)


# ═══════════════════════════════════════════════════════════════════
# Time helpers
# ═══════════════════════════════════════════════════════════════════

class TestParseTime(TestCase):
    def test_midnight(self):
        self.assertEqual(balance_utils.parse_time("00:00"), 0)

    def test_noon(self):
        self.assertEqual(balance_utils.parse_time("12:00"), 720)

    def test_end_of_day(self):
        self.assertEqual(balance_utils.parse_time("23:59"), 1439)

    def test_morning(self):
        self.assertEqual(balance_utils.parse_time("08:30"), 510)


class TestFmtMinutes(TestCase):
    def test_midnight(self):
        self.assertEqual(balance_utils.fmt_minutes(0), "00:00")

    def test_noon(self):
        self.assertEqual(balance_utils.fmt_minutes(720), "12:00")

    def test_afternoon(self):
        self.assertEqual(balance_utils.fmt_minutes(930), "15:30")


class TestGetWindows(TestCase):
    def test_new_format(self):
        sched = {"windows": [{"start": "08:00", "end": "18:00"}]}
        self.assertEqual(balance_utils.get_windows(sched), [(480, 1080)])

    def test_multi_window(self):
        sched = {
            "windows": [
                {"start": "08:00", "end": "10:30"},
                {"start": "16:00", "end": "19:00"},
            ]
        }
        result = balance_utils.get_windows(sched)
        self.assertEqual(result, [(480, 630), (960, 1140)])

    def test_legacy_format(self):
        sched = {"start_hour": 9, "end_hour": 17}
        self.assertEqual(balance_utils.get_windows(sched), [(540, 1020)])

    def test_legacy_with_minutes(self):
        sched = {"start_hour": 8, "start_minute": 30, "end_hour": 17, "end_minute": 45}
        self.assertEqual(balance_utils.get_windows(sched), [(510, 1065)])


# ═══════════════════════════════════════════════════════════════════
# Schedule finding
# ═══════════════════════════════════════════════════════════════════

class TestFindSchedule(TestCase):
    def test_weekday(self):
        name, sched = balance_utils.find_schedule(SAMPLE_CONFIG, 2)  # Tuesday
        self.assertEqual(name, "weekday")
        self.assertIsNotNone(sched)

    def test_saturday(self):
        name, sched = balance_utils.find_schedule(SAMPLE_CONFIG, 6)
        self.assertEqual(name, "saturday")

    def test_sunday_unscheduled(self):
        name, sched = balance_utils.find_schedule(SAMPLE_CONFIG, 7)
        self.assertIsNone(name)
        self.assertIsNone(sched)

    def test_all_weekdays(self):
        for day in range(1, 6):
            name, _ = balance_utils.find_schedule(SAMPLE_CONFIG, day)
            self.assertEqual(name, "weekday", f"Day {day} should match weekday")


# ═══════════════════════════════════════════════════════════════════
# Window checks
# ═══════════════════════════════════════════════════════════════════

class TestInAnyWindow(TestCase):
    def setUp(self):
        self.single = [(480, 1080)]  # 08:00-18:00
        self.multi = [(480, 630), (960, 1140)]  # 08:00-10:30 + 16:00-19:00

    def test_inside_single(self):
        inside, start, end = balance_utils.in_any_window(self.single, 600)  # 10:00
        self.assertTrue(inside)
        self.assertEqual(start, 480)
        self.assertEqual(end, 1080)

    def test_before_single(self):
        inside, start, end = balance_utils.in_any_window(self.single, 420)  # 07:00
        self.assertFalse(inside)
        self.assertIsNone(start)

    def test_after_single(self):
        inside, _, _ = balance_utils.in_any_window(self.single, 1100)  # 18:20
        self.assertFalse(inside)

    def test_at_start_boundary(self):
        inside, _, _ = balance_utils.in_any_window(self.single, 480)  # 08:00 exactly
        self.assertTrue(inside)

    def test_at_end_boundary(self):
        inside, _, _ = balance_utils.in_any_window(self.single, 1080)  # 18:00 exactly
        self.assertFalse(inside)  # end is exclusive

    def test_multi_first_window(self):
        inside, start, end = balance_utils.in_any_window(self.multi, 540)  # 09:00
        self.assertTrue(inside)
        self.assertEqual(start, 480)
        self.assertEqual(end, 630)

    def test_multi_second_window(self):
        inside, start, end = balance_utils.in_any_window(self.multi, 1000)  # 16:40
        self.assertTrue(inside)
        self.assertEqual(start, 960)
        self.assertEqual(end, 1140)

    def test_multi_gap(self):
        inside, _, _ = balance_utils.in_any_window(self.multi, 700)  # 11:40 — gap
        self.assertFalse(inside)


class TestNextWindowToday(TestCase):
    def test_next_window_exists(self):
        windows = [(480, 630), (960, 1140)]
        result = balance_utils.next_window_today(windows, 700)  # 11:40
        self.assertEqual(result, (960, 1140))

    def test_no_more_windows(self):
        windows = [(480, 630), (960, 1140)]
        result = balance_utils.next_window_today(windows, 1200)  # 20:00
        self.assertIsNone(result)

    def test_before_all_windows(self):
        windows = [(480, 630), (960, 1140)]
        result = balance_utils.next_window_today(windows, 300)  # 05:00
        self.assertEqual(result, (480, 630))


class TestNextAvailable(TestCase):
    def test_later_today_same_schedule(self):
        # Saturday at 12:00 — second window starts at 16:00
        now = datetime(2026, 2, 28, 12, 0)  # Saturday
        result = balance_utils.next_available(SAMPLE_CONFIG, now)
        self.assertEqual(result, "today at 16:00")

    def test_next_day(self):
        # Sunday at 10:00 — no Sunday schedule, next is Monday
        now = datetime(2026, 3, 1, 10, 0)  # Sunday
        result = balance_utils.next_available(SAMPLE_CONFIG, now)
        self.assertEqual(result, "Monday at 08:00")

    def test_after_all_windows_today(self):
        # Tuesday at 20:00 — next is Wednesday 08:00
        now = datetime(2026, 2, 24, 20, 0)
        result = balance_utils.next_available(SAMPLE_CONFIG, now)
        self.assertEqual(result, "Wednesday at 08:00")


class TestWindowsSummary(TestCase):
    def test_single(self):
        result = balance_utils.windows_summary([(480, 1080)])
        self.assertIn("08:00", result)
        self.assertIn("18:00", result)

    def test_multi(self):
        result = balance_utils.windows_summary([(480, 630), (960, 1140)])
        self.assertIn("08:00", result)
        self.assertIn("10:30", result)
        self.assertIn("16:00", result)
        self.assertIn("19:00", result)
        self.assertIn("+", result)


# ═══════════════════════════════════════════════════════════════════
# Usage tracking
# ═══════════════════════════════════════════════════════════════════

class TestUsageTracking(TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_usage_dir = balance_utils.USAGE_DIR
        balance_utils.USAGE_DIR = self.tmpdir

    def tearDown(self):
        balance_utils.USAGE_DIR = self._orig_usage_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_usage_file(self):
        now = make_dt(hour=10)
        self.assertEqual(balance_utils.get_active_minutes(now), 0)

    def test_record_and_count(self):
        now = make_dt(hour=10, minute=0)
        balance_utils.record_prompt(now)
        self.assertEqual(balance_utils.get_active_minutes(now), 1)

    def test_dedup_same_minute(self):
        now = make_dt(hour=10, minute=0)
        balance_utils.record_prompt(now)
        balance_utils.record_prompt(now)
        balance_utils.record_prompt(now)
        self.assertEqual(balance_utils.get_active_minutes(now), 1)

    def test_different_minutes(self):
        for m in range(5):
            now = make_dt(hour=10, minute=m)
            balance_utils.record_prompt(now)
        self.assertEqual(balance_utils.get_active_minutes(make_dt(hour=10)), 5)

    def test_cleanup_old_files(self):
        now = make_dt()
        for days_ago in range(10):
            dt = now - timedelta(days=days_ago)
            date_str = dt.strftime("%Y-%m-%d")
            (self.tmpdir / f"{date_str}.log").write_text("10:00\n")
            (self.tmpdir / f"{date_str}.extensions.json").write_text("{}")

        balance_utils.cleanup_old_usage(now, keep_days=7)

        remaining_logs = list(self.tmpdir.glob("*.log"))
        remaining_ext = list(self.tmpdir.glob("*.extensions.json"))
        self.assertEqual(len(remaining_logs), 8)
        self.assertEqual(len(remaining_ext), 8)

    def test_maybe_cleanup_runs_once_per_day(self):
        now = make_dt()
        balance_utils.maybe_cleanup(now)
        marker = self.tmpdir / ".last_cleanup"
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text().strip(), now.strftime("%Y-%m-%d"))

        old_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")
        old_file = self.tmpdir / f"{old_date}.log"
        old_file.write_text("09:00\n")

        balance_utils.maybe_cleanup(now)
        self.assertTrue(old_file.exists())  # Skipped — marker already set for today

        tomorrow = now + timedelta(days=1)
        balance_utils.maybe_cleanup(tomorrow)
        self.assertFalse(old_file.exists())  # Cleaned on next day


# ═══════════════════════════════════════════════════════════════════
# Extensions
# ═══════════════════════════════════════════════════════════════════

class TestExtensions(TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_usage_dir = balance_utils.USAGE_DIR
        balance_utils.USAGE_DIR = self.tmpdir

    def tearDown(self):
        balance_utils.USAGE_DIR = self._orig_usage_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_extensions_used(self):
        now = make_dt()
        self.assertEqual(balance_utils.count_extensions_today(now, "quick"), 0)

    def test_record_and_count_extension(self):
        now = make_dt()
        balance_utils.record_extension(now, "quick")
        self.assertEqual(balance_utils.count_extensions_today(now, "quick"), 1)
        self.assertEqual(balance_utils.count_extensions_today(now, "more"), 0)

    def test_multiple_extensions(self):
        now = make_dt()
        balance_utils.record_extension(now, "quick")
        balance_utils.record_extension(now, "quick")
        balance_utils.record_extension(now, "more")
        self.assertEqual(balance_utils.count_extensions_today(now, "quick"), 2)
        self.assertEqual(balance_utils.count_extensions_today(now, "more"), 1)


# ═══════════════════════════════════════════════════════════════════
# Override checking
# ═══════════════════════════════════════════════════════════════════

class TestOverride(TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.override_file = self.tmpdir / "override.json"
        self.config = {
            **SAMPLE_CONFIG,
            "override": {
                "env_var": "TEST_BALANCE_OVERRIDE",
                "file": str(self.override_file),
            },
        }
        os.environ.pop("TEST_BALANCE_OVERRIDE", None)

    def tearDown(self):
        os.environ.pop("TEST_BALANCE_OVERRIDE", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_override(self):
        now = make_dt()
        active, info = balance_utils.check_override(self.config, now)
        self.assertFalse(active)

    def test_env_var_override(self):
        os.environ["TEST_BALANCE_OVERRIDE"] = "1"
        now = make_dt()
        active, info = balance_utils.check_override(self.config, now)
        self.assertTrue(active)
        self.assertIn("environment", info)

    def test_env_var_true(self):
        os.environ["TEST_BALANCE_OVERRIDE"] = "true"
        now = make_dt()
        active, _ = balance_utils.check_override(self.config, now)
        self.assertTrue(active)

    def test_env_var_no(self):
        os.environ["TEST_BALANCE_OVERRIDE"] = "no"
        now = make_dt()
        active, _ = balance_utils.check_override(self.config, now)
        self.assertFalse(active)

    def test_file_override_valid(self):
        now = make_dt(hour=10)
        expires = now + timedelta(minutes=30)
        data = {
            "type": "quick",
            "label": "Quick 15-min session",
            "expires_at": expires.isoformat(),
        }
        self.override_file.write_text(json.dumps(data))
        active, info = balance_utils.check_override(self.config, now)
        self.assertTrue(active)
        self.assertIn("Quick", info)
        self.assertIn("remaining", info)

    def test_file_override_expired(self):
        now = make_dt(hour=10)
        expires = now - timedelta(minutes=5)
        data = {"type": "quick", "expires_at": expires.isoformat()}
        self.override_file.write_text(json.dumps(data))
        active, _ = balance_utils.check_override(self.config, now)
        self.assertFalse(active)
        self.assertFalse(self.override_file.exists())  # Expired file cleaned up


# ═══════════════════════════════════════════════════════════════════
# Config loading
# ═══════════════════════════════════════════════════════════════════

class TestLoadConfig(TestCase):
    def setUp(self):
        self._orig_path = balance_utils.CONFIG_PATH
        self.tmpdir = Path(tempfile.mkdtemp())
        self.config_file = self.tmpdir / "config.json"
        balance_utils.CONFIG_PATH = self.config_file

    def tearDown(self):
        balance_utils.CONFIG_PATH = self._orig_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_config_uses_defaults(self):
        config = balance_utils.load_config()
        self.assertTrue(config["enabled"])
        self.assertEqual(config["timezone"], "Europe/London")
        self.assertIn("weekday", config["schedule"])

    def test_custom_config_overrides(self):
        custom = {
            "enabled": True,
            "timezone": "America/New_York",
            "schedule": {
                "weekday": {
                    "days": [1, 2, 3, 4, 5],
                    "windows": [{"start": "09:00", "end": "17:00"}],
                    "daily_limit_minutes": 120,
                },
            },
        }
        self.config_file.write_text(json.dumps(custom))
        config = balance_utils.load_config()
        self.assertEqual(config["timezone"], "America/New_York")
        self.assertEqual(config["schedule"]["weekday"]["daily_limit_minutes"], 120)
        self.assertIn("quick", config["extensions"])  # Defaults preserved

    def test_malformed_json_uses_defaults(self):
        self.config_file.write_text("not json {{{")
        config = balance_utils.load_config()
        self.assertTrue(config["enabled"])


# ═══════════════════════════════════════════════════════════════════
# Hook enforcement (integration-style)
# ═══════════════════════════════════════════════════════════════════

class TestHookEnforcement(TestCase):
    def setUp(self):
        import balance_hook
        self.tr = balance_hook

        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_usage_dir = balance_utils.USAGE_DIR
        balance_utils.USAGE_DIR = self.tmpdir

    def tearDown(self):
        balance_utils.USAGE_DIR = self._orig_usage_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_weekday_in_window(self):
        now = make_dt(hour=10)  # Tuesday 10:00
        in_win, name, sched, end_m, msg = self.tr.check_window(SAMPLE_CONFIG, now)
        self.assertTrue(in_win)
        self.assertEqual(name, "weekday")
        self.assertEqual(end_m, 1080)  # 18:00

    def test_weekday_before_window(self):
        now = make_dt(hour=6)  # Tuesday 06:00
        in_win, _, _, _, msg = self.tr.check_window(SAMPLE_CONFIG, now)
        self.assertFalse(in_win)
        self.assertIn("Outside allowed hours", msg)

    def test_weekday_after_window(self):
        now = make_dt(hour=20)  # Tuesday 20:00
        in_win, _, _, _, msg = self.tr.check_window(SAMPLE_CONFIG, now)
        self.assertFalse(in_win)
        self.assertIn("Outside allowed hours", msg)

    def test_sunday_blocked(self):
        now = datetime(2026, 3, 1, 10, 0)  # Sunday
        in_win, _, _, _, msg = self.tr.check_window(SAMPLE_CONFIG, now)
        self.assertFalse(in_win)
        self.assertIn("offline today", msg)

    def test_saturday_first_window(self):
        now = datetime(2026, 2, 28, 9, 0)  # Saturday 09:00
        in_win, name, _, end_m, _ = self.tr.check_window(SAMPLE_CONFIG, now)
        self.assertTrue(in_win)
        self.assertEqual(name, "saturday")
        self.assertEqual(end_m, 630)  # 10:30

    def test_saturday_gap(self):
        now = datetime(2026, 2, 28, 12, 0)  # Saturday 12:00 — in gap
        in_win, _, _, _, msg = self.tr.check_window(SAMPLE_CONFIG, now)
        self.assertFalse(in_win)
        self.assertIn("Outside allowed hours", msg)

    def test_saturday_second_window(self):
        now = datetime(2026, 2, 28, 17, 0)  # Saturday 17:00
        in_win, _, _, end_m, _ = self.tr.check_window(SAMPLE_CONFIG, now)
        self.assertTrue(in_win)
        self.assertEqual(end_m, 1140)  # 19:00

    def test_cap_not_hit(self):
        now = make_dt(hour=10)
        _, sched = balance_utils.find_schedule(SAMPLE_CONFIG, now.isoweekday())
        ok, used, limit, msg = self.tr.check_daily_cap(SAMPLE_CONFIG, sched, now)
        self.assertTrue(ok)
        self.assertEqual(used, 0)
        self.assertEqual(limit, 240)

    def test_cap_hit(self):
        now = make_dt(hour=10)
        for m in range(240):
            h = 8 + m // 60
            minute = m % 60
            balance_utils.record_prompt(make_dt(hour=h, minute=minute))

        _, sched = balance_utils.find_schedule(SAMPLE_CONFIG, now.isoweekday())
        ok, used, limit, msg = self.tr.check_daily_cap(SAMPLE_CONFIG, sched, now)
        self.assertFalse(ok)
        self.assertEqual(used, 240)
        self.assertIn("Daily limit reached", msg)

    def test_no_cap_configured(self):
        config = {
            **SAMPLE_CONFIG,
            "schedule": {
                "weekday": {
                    "days": [1, 2, 3, 4, 5],
                    "windows": [{"start": "08:00", "end": "18:00"}],
                }
            },
        }
        now = make_dt(hour=10)
        _, sched = balance_utils.find_schedule(config, now.isoweekday())
        ok, _, limit, _ = self.tr.check_daily_cap(config, sched, now)
        self.assertTrue(ok)
        self.assertIsNone(limit)


# ═══════════════════════════════════════════════════════════════════
# Warnings
# ═══════════════════════════════════════════════════════════════════

class TestWarnings(TestCase):
    def setUp(self):
        import balance_hook
        self.tr = balance_hook

    def test_window_closing_warning(self):
        now = make_dt(hour=17, minute=50)  # 10 min before 18:00
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 100, 240)
        self.assertTrue(any("Window closes" in w for w in warnings))

    def test_window_warning_at_exact_threshold(self):
        now = make_dt(hour=17, minute=45)  # exactly 15 min before 18:00
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 100, 240)
        self.assertTrue(any("Window closes" in w for w in warnings))

    def test_no_window_warning_when_far(self):
        now = make_dt(hour=10, minute=0)
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 100, 240)
        self.assertFalse(any("Window closes" in w for w in warnings))

    def test_no_window_warning_one_minute_outside_threshold(self):
        now = make_dt(hour=17, minute=44)  # 16 min before — just outside threshold
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 100, 240)
        self.assertFalse(any("Window closes" in w for w in warnings))

    def test_cap_approaching_warning(self):
        now = make_dt(hour=10)
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 220, 240)  # 20 min left
        self.assertTrue(any("Daily usage" in w for w in warnings))

    def test_no_cap_warning_when_far(self):
        now = make_dt(hour=10)
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 100, 240)
        self.assertFalse(any("Daily usage" in w for w in warnings))

    def test_both_warnings(self):
        now = make_dt(hour=17, minute=50)  # Near window end AND near cap
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 230, 240)
        self.assertEqual(len(warnings), 2)

    def test_no_warning_when_active_end_none(self):
        now = make_dt(hour=17, minute=50)
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, None, 100, 240)
        self.assertFalse(any("Window closes" in w for w in warnings))

    def test_warning_message_contains_minutes(self):
        now = make_dt(hour=17, minute=53)  # 7 min before close
        warnings = self.tr.build_warnings(SAMPLE_CONFIG, now, 1080, 100, 240)
        self.assertTrue(any("7 minutes" in w for w in warnings))


# ═══════════════════════════════════════════════════════════════════
# Extension menu (block message)
# ═══════════════════════════════════════════════════════════════════

class TestExtensionMenu(TestCase):
    def setUp(self):
        import balance_hook
        self.tr = balance_hook
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_usage_dir = balance_utils.USAGE_DIR
        balance_utils.USAGE_DIR = self.tmpdir

    def tearDown(self):
        balance_utils.USAGE_DIR = self._orig_usage_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_shows_full_path_to_extend_cmd(self):
        """Block message must show full path so user can copy-paste from terminal."""
        now = make_dt()
        msg = self.tr.extension_menu(SAMPLE_CONFIG, now, "Blocked.")
        self.assertIn("balance-extend", msg)
        # Must be a full path, not a bare command
        self.assertIn("/", msg.split("balance-extend")[0].split("\n")[-1])

    def test_shows_available_extensions(self):
        now = make_dt()
        msg = self.tr.extension_menu(SAMPLE_CONFIG, now, "Blocked.")
        self.assertIn("quick", msg)
        self.assertIn("more", msg)
        self.assertIn("2 remaining", msg)
        self.assertIn("3 remaining", msg)

    def test_shows_run_from_terminal_label(self):
        now = make_dt()
        msg = self.tr.extension_menu(SAMPLE_CONFIG, now, "Blocked.")
        self.assertIn("Run from terminal", msg)

    def test_context_string_included(self):
        now = make_dt()
        msg = self.tr.extension_menu(SAMPLE_CONFIG, now, "Outside hours.")
        self.assertTrue(msg.startswith("Outside hours."))

    def test_shows_none_left_when_exhausted(self):
        now = make_dt()
        for _ in range(2):
            balance_utils.record_extension(now, "quick")
        for _ in range(3):
            balance_utils.record_extension(now, "more")

        msg = self.tr.extension_menu(SAMPLE_CONFIG, now, "Blocked.")
        self.assertIn("none left", msg)
        self.assertIn("No extensions remaining", msg)


# ═══════════════════════════════════════════════════════════════════
# Hook stdout flushing (regression: warnings were silently dropped)
# ═══════════════════════════════════════════════════════════════════

class TestHookOutput(TestCase):
    """Integration tests against the hook process via stdin/stdout."""

    def _run_hook(self, prompt="test", env=None):
        import subprocess
        hook_path = REPO_ROOT / "balance_hook.py"
        input_data = json.dumps({"prompt": prompt})
        result = subprocess.run(
            ["python3", str(hook_path)],
            input=input_data,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
        )
        return result

    def test_override_active_outputs_context(self):
        """When override is active, hook must output additionalContext to stdout."""
        from datetime import datetime, timedelta
        expires = datetime.now() + timedelta(minutes=30)
        override_data = json.dumps({
            "type": "quick",
            "label": "Quick 15-min session",
            "expires_at": expires.isoformat(),
        })
        override_path = Path(tempfile.mktemp(suffix=".json"))
        try:
            override_path.write_text(override_data)
            # Point the hook at our temp override file via env var
            result = self._run_hook(env={"BALANCE_OVERRIDE": "1"})
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout.strip(), "Hook produced no stdout with active override")
            data = json.loads(result.stdout.strip())
            self.assertIn("additionalContext", data)
        finally:
            override_path.unlink(missing_ok=True)

    def test_blocked_outputs_to_stderr(self):
        """When blocked, error message must go to stderr not stdout."""
        # Run with override disabled to ensure we hit a block if outside hours
        # This test is environment-dependent; just verify stderr is used on block
        result = self._run_hook()
        if result.returncode == 2:
            self.assertTrue(result.stderr.strip(), "Block message must go to stderr")
            self.assertEqual(result.stdout.strip(), "", "Blocked hook must not write to stdout")

    def test_allowed_exit_zero(self):
        """When override is active, hook exits 0."""
        result = self._run_hook(env={"BALANCE_OVERRIDE": "1"})
        self.assertEqual(result.returncode, 0)


# ═══════════════════════════════════════════════════════════════════
# HAL mode (balance-extend CLI)
# ═══════════════════════════════════════════════════════════════════

class TestHalMode(TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_usage_dir = balance_utils.USAGE_DIR
        balance_utils.USAGE_DIR = self.tmpdir

        import importlib.util, types
        cli_path = str(REPO_ROOT / "balance-extend")
        loader = importlib.machinery.SourceFileLoader("balance_extend", cli_path)
        self.cli = types.ModuleType(loader.name)
        loader.exec_module(self.cli)

    def tearDown(self):
        balance_utils.USAGE_DIR = self._orig_usage_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_total_extensions_counts_all_types(self):
        now = make_dt()
        balance_utils.record_extension(now, "quick")
        balance_utils.record_extension(now, "more")
        balance_utils.record_extension(now, "more")
        self.assertEqual(self.cli.total_extensions_today(SAMPLE_CONFIG, now), 3)

    def test_no_hal_under_threshold(self):
        now = make_dt()
        balance_utils.record_extension(now, "quick")
        total = self.cli.total_extensions_today(SAMPLE_CONFIG, now)
        self.assertLess(total, 2)

    def test_hal_triggers_at_threshold(self):
        now = make_dt()
        balance_utils.record_extension(now, "quick")
        balance_utils.record_extension(now, "more")
        total = self.cli.total_extensions_today(SAMPLE_CONFIG, now)
        self.assertGreaterEqual(total, 2)

    def test_hal_stage_escalation(self):
        self.assertEqual(min(2 - 2, len(self.cli.HAL_STAGES) - 1), 0)
        self.assertEqual(min(3 - 2, len(self.cli.HAL_STAGES) - 1), 1)
        self.assertEqual(min(4 - 2, len(self.cli.HAL_STAGES) - 1), 2)
        self.assertEqual(min(10 - 2, len(self.cli.HAL_STAGES) - 1), 2)  # Caps at max

    def test_hal_correct_passphrase_returns_true(self):
        now = make_dt()
        balance_utils.record_extension(now, "quick")
        balance_utils.record_extension(now, "more")
        with patch("builtins.input", return_value="i'm sorry hal"):
            result = self.cli.hal_mode(SAMPLE_CONFIG, now)
        self.assertTrue(result)

    def test_hal_wrong_passphrase_returns_false(self):
        now = make_dt()
        balance_utils.record_extension(now, "quick")
        balance_utils.record_extension(now, "more")
        with patch("builtins.input", return_value="let me in"):
            result = self.cli.hal_mode(SAMPLE_CONFIG, now)
        self.assertFalse(result)

    def test_hal_stage_1_passphrase(self):
        now = make_dt()
        for _ in range(3):
            balance_utils.record_extension(now, "quick")
        with patch("builtins.input", return_value="open the pod bay doors"):
            result = self.cli.hal_mode(SAMPLE_CONFIG, now)
        self.assertTrue(result)

    def test_hal_stage_2_passphrase(self):
        now = make_dt()
        for _ in range(4):
            balance_utils.record_extension(now, "quick")
        with patch("builtins.input", return_value="my mind is going i can feel it"):
            result = self.cli.hal_mode(SAMPLE_CONFIG, now)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest_main(verbosity=2)
