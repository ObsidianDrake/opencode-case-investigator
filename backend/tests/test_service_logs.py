import logging
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

from service_logs import DailyLog


class LogRotationTests(unittest.TestCase):
    def test_rollover_after_idle_days_and_purge_by_date(self):
        with tempfile.TemporaryDirectory() as directory:
            active = Path(directory) / "case-investigator.log"
            def archive(age):
                return active.with_suffix(".log." + (date.today() - timedelta(days=age)).isoformat())

            active.write_text("previous run\n")
            yesterday = archive(1)
            modified = datetime.combine(date.today() - timedelta(days=1), time(12)).timestamp()
            os.utime(active, (modified, modified))
            boundary, expired = archive(14), archive(15)
            unrelated = active.with_suffix(".log.notes")
            for path in (boundary, expired, unrelated):
                path.touch()
            handler = DailyLog(active, when="midnight", backupCount=14, encoding="utf-8")
            self.addCleanup(handler.close)
            record = logging.makeLogRecord({"msg": "current run"})
            handler.handle(record)
            self.assertEqual(yesterday.read_text(), "previous run\n")
            self.assertEqual(active.read_text(), "current run\n")
            self.assertTrue(boundary.exists())
            self.assertFalse(expired.exists())
            self.assertTrue(unrelated.exists())
