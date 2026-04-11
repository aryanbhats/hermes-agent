#!/usr/bin/env python3
"""Tests for health_log SQLite event library."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "health" / "daily-health" / "scripts"))


class TestHealthLog(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_health.db")
        os.environ["HEALTH_DB_PATH"] = self.db_path
        import importlib
        import health_log
        importlib.reload(health_log)
        self.hl = health_log

    def tearDown(self):
        os.environ.pop("HEALTH_DB_PATH", None)

    def test_log_event_basic(self):
        self.hl.log_event(type="habit", subtype="walk", source="user", value=25, unit="min")
        events = self.hl.today_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "habit")
        self.assertEqual(events[0]["subtype"], "walk")
        self.assertEqual(events[0]["source"], "user")
        self.assertAlmostEqual(events[0]["value"], 25.0)

    def test_log_event_with_data_json(self):
        data = {"recovery": 95, "hrv": 94.8}
        self.hl.log_event(type="checkin", source="system", data=data)
        events = self.hl.today_events()
        self.assertEqual(len(events), 1)
        parsed = json.loads(events[0]["data"])
        self.assertEqual(parsed["recovery"], 95)

    def test_timestamps_are_utc(self):
        self.hl.log_event(type="habit", subtype="supplement", source="user")
        events = self.hl.today_events()
        ts = events[0]["ts"]
        self.assertTrue(ts.endswith("Z") or "+00:00" in ts, f"Timestamp not UTC: {ts}")

    def test_last_user_interaction_ignores_system(self):
        self.hl.log_event(type="checkin", source="system")
        result = self.hl.last_user_interaction()
        self.assertIsNone(result, "System events should not count as user interaction")
        self.hl.log_event(type="response", source="user")
        result = self.hl.last_user_interaction()
        self.assertIsNotNone(result)

    def test_check_morning_response_false(self):
        self.hl.log_event(type="checkin", source="system")
        self.assertFalse(self.hl.check_morning_response())

    def test_check_morning_response_true(self):
        self.hl.log_event(type="checkin", source="system")
        self.hl.log_event(type="response", source="user")
        self.assertTrue(self.hl.check_morning_response())

    def test_events_range(self):
        self.hl.log_event(type="habit", subtype="walk", source="user", value=20)
        events = self.hl.events_range(7)
        self.assertEqual(len(events), 1)

    def test_system_event_idempotency(self):
        self.hl.log_event(type="checkin", source="system", data={"recovery": 90})
        self.hl.log_event(type="checkin", source="system", data={"recovery": 95})
        events = self.hl.today_events()
        system_checkins = [e for e in events if e["type"] == "checkin" and e["source"] == "system"]
        self.assertEqual(len(system_checkins), 1, "Duplicate system checkin should be ignored")

    def test_wal_mode_enabled(self):
        import sqlite3
        # Trigger DB creation via the module so WAL is set
        self.hl.log_event(type="habit", subtype="walk", source="user")
        conn = sqlite3.connect(self.db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        self.assertEqual(mode, "wal")

    def test_concurrent_writes(self):
        errors = []
        def writer(source, n):
            try:
                for i in range(n):
                    self.hl.log_event(type="habit", subtype="walk", source=source, value=float(i), note=f"thread-{source}-{i}")
            except Exception as e:
                errors.append(e)
        t1 = threading.Thread(target=writer, args=("user", 20))
        t2 = threading.Thread(target=writer, args=("user", 20))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(len(errors), 0, f"Concurrent write errors: {errors}")
        events = self.hl.today_events()
        self.assertEqual(len(events), 40)
