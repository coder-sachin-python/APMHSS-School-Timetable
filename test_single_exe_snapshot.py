"""Verify first-launch DB extraction for the single-file build."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from timetable_core import Store
from BUILD_SINGLE_EXE import valid_database

class SingleExeTests(unittest.TestCase):
    def test_bundled_snapshot_copies_to_local_app_data_and_preserves_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / 'bundle' / 'data' / 'school_timetable.sqlite3'
            bundled.parent.mkdir(parents=True)
            old = Store(bundled)
            old.add_user('admin', 'Password123', 'admin')
            old.save_weekly_plan('12', 'B1', {'Tamil': 5, 'English': 5})
            old.close()
            self.assertTrue(valid_database(bundled))
            fake_exe = root / 'on_pc' / 'APMHSS_Time_Table.exe'
            fake_exe.parent.mkdir()
            fake_exe.touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'LocalAppData')}), \
                 patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, '_MEIPASS', str(root / 'bundle'), create=True), \
                 patch.object(sys, 'executable', str(fake_exe)):
                store = Store()
                self.assertEqual(store.login('admin', 'Password123'), 'admin')
                self.assertEqual(store.weekly_plan('12', 'B1')['Tamil'], 5)
                self.assertEqual(store.path, (root / 'LocalAppData' / 'APMHSS_Time_Table' / 'school_timetable.sqlite3').resolve())
                store.set_password('admin', 'NewPassword123')
                store.close()
                reopened = Store()
                self.assertEqual(reopened.login('admin', 'NewPassword123'), 'admin')
                self.assertIsNone(reopened.login('admin', 'Password123'))
                reopened.close()
            # Embedded snapshot stays unchanged even though local DB changed.
            snapshot = Store(bundled)
            self.assertEqual(snapshot.login('admin', 'Password123'), 'admin')
            snapshot.close()

    def test_existing_local_data_not_overwritten_by_embedded_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / 'LocalAppData' / 'APMHSS_Time_Table' / 'school_timetable.sqlite3'
            local.parent.mkdir(parents=True)
            original = Store(local)
            original.add_user('admin', 'ExistingPassword456', 'admin')
            original.close()
            bundled = root / 'bundle' / 'data' / 'school_timetable.sqlite3'
            bundled.parent.mkdir(parents=True)
            other = Store(bundled)
            other.add_user('admin', 'BundledPassword456', 'admin')
            other.close()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'LocalAppData')}), \
                 patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, '_MEIPASS', str(root / 'bundle'), create=True), \
                 patch.object(sys, 'executable', str(root / 'APMHSS_Time_Table.exe')):
                store = Store()
                self.assertEqual(store.login('admin', 'ExistingPassword456'), 'admin')
                self.assertIsNone(store.login('admin', 'BundledPassword456'))
                store.close()

if __name__ == '__main__':
    unittest.main()
