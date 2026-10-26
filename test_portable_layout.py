import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from timetable_core import Store, Scheduler, TimetableError
from timetable_export import table_data

CTX = ('2026-2027', 'October', 'Week 1', '12', 'B1')

class PortableLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'data' / 'school_timetable.sqlite3'
        self.store = Store(self.path)
        self.store.add_user('admin', 'Password123', 'admin')
        self.plan = {'Maths': 6, 'English': 6, 'Tamil': 6, 'Physics': 6, 'Chemistry': 6, 'Computer': 4, 'PET': 2}
        self.store.save_weekly_plan('12', 'B1', self.plan)
        self.store.save(CTX, Scheduler(self.store).generate(CTX))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_layout_rows_columns_times_and_password_survive_reopen(self):
        times = [('08:30','09:15'), ('09:15','10:00'), ('10:10','10:55'),
                 ('11:05','11:50'), ('12:00','12:45'), ('13:30','14:15'), ('14:15','15:00')]
        self.assertEqual(self.store.apply_layout_and_regenerate('12','B1',6,times,CTX),1)
        self.assertEqual(self.store.capacity('12','B1'),42)
        self.assertEqual(len(self.store.load(CTX)),42)
        self.assertEqual(len(table_data(CTX,self.store.load(CTX),self.store)[0]),8)
        self.store.set_password('admin','NewPassword789')
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.login('admin','NewPassword789'),'admin')
        self.assertIsNone(self.store.login('admin','Password123'))
        self.assertEqual(self.store.layout('12','B1')[1],times)
        self.assertEqual(len(self.store.load(CTX)),42)
        self.assertEqual(self.store.weekly_plan('12','B1'),self.plan)
        self.assertNotIn('NewPassword789', self.path.read_bytes().decode('latin-1'))

    def test_reduced_capacity_rollback(self):
        old = self.store.load(CTX)
        with self.assertRaises(TimetableError):
            self.store.apply_layout_and_regenerate('12','B1',3,[('09:00','09:45')]*6,CTX)
        self.assertEqual(self.store.load(CTX),old)
        self.assertEqual(self.store.capacity('12','B1'),36)

    def test_overlapping_periods_rejected(self):
        with self.assertRaises(TimetableError):
            self.store.validate_layout(6,[('08:30','09:15'),('09:00','09:45')])
        with self.assertRaises(TimetableError):
            self.store.validate_layout(6,[('13:00','12:00')])

    def test_cross_class_overlapping_timings(self):
        self.store.assign('12','B1','Maths','Teacher A')
        self.store.assign('12','A','English','Teacher A')
        self.store.apply_layout_and_regenerate('12','A',6,[('08:45','09:30'),('09:30','10:50'),('11:00','12:30'),('13:00','14:20'),('14:30','15:30'),('15:30','16:30')],('2026-2027','October','Week 1','12','A'))
        # Cross-section checks must use actual 24-hour intervals.
        self.assertTrue(self.store.periods_overlap('12','B1',0,'12','A',0))
        self.assertFalse(self.store.periods_overlap('12','B1',0,'12','A',4))

    def test_legacy_database_backup_import_pattern(self):
        with tempfile.TemporaryDirectory() as d:
            destination=Path(d)/'moved.sqlite3'
            self.store.backup(destination)
            new=Store(destination)
            self.assertEqual(new.login('admin','Password123'),'admin')
            self.assertEqual(new.weekly_plan('12','B1'),self.plan)
            new.close()

if __name__=='__main__': unittest.main()

class LegacyAccountMigrationTests(unittest.TestCase):
    def test_old_digest_column_upgraded_without_resetting_password(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.sqlite3'
            salt = os.urandom(16)
            digest = hashlib.pbkdf2_hmac('sha256', b'OldPassword123', salt, 250000)
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE users (username TEXT PRIMARY KEY, salt BLOB, digest BLOB)')
                db.execute('INSERT INTO users VALUES (?,?,?)', ('admin',salt,digest))
                db.commit()
            db = Store(path)
            try:
                self.assertEqual(db.login('admin','OldPassword123'),'admin')
                db.set_password('admin','ChangedPassword456')
                self.assertEqual(db.login('admin','ChangedPassword456'),'admin')
            finally:
                db.close()
