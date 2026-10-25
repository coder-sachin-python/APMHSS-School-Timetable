"""Run: python -m unittest -v test_timetable.py"""
import tempfile
import unittest
from pathlib import Path
from timetable_core import Store,Scheduler,Slot,TimetableError,overlap,PERIODS,group,school_days
from timetable_export import export_word,export_excel,export_pdf

CTX=('2026-2027','October','Week 1','8','A')

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        self.db=Store(self.folder/'test.sqlite3')
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def test_first_run_login_hashed(self):
        self.assertFalse(self.db.has_users())
        self.db.add_user('admin','MyGoodPass123','admin')
        self.assertEqual(self.db.login('admin','MyGoodPass123'),'admin')
        self.assertIsNone(self.db.login('admin','bad'))
        with self.assertRaises(TimetableError):self.db.add_user('wrong','easy','viewer')
        users=self.db.db.execute('SELECT password_hash FROM users').fetchone()
        self.assertNotIn(b'MyGoodPass123',bytes(users[0]))
    def test_generate_save_reopen(self):
        slots=Scheduler(self.db).generate(CTX)
        self.assertEqual(len(slots),35)
        self.assertEqual(slots[('Friday',5)].subject,'SKKA')
        self.assertEqual(slots[('Friday',6)].subject,'SKKA')
        self.db.save(CTX,slots)
        self.db.close()
        self.db=Store(self.folder/'test.sqlite3')
        self.assertEqual(self.db.load(CTX),slots)
        self.assertEqual(len(self.db.sections('8')),3)
    def test_preserve_other_class_and_conflicts(self):
        a=CTX;b=(*CTX[:3],'9','A')
        self.db.assign('8','A','Tamil','Same Teacher')
        self.db.assign('9','A','English','Same Teacher')
        self.db.save(a,{('Monday',0):Slot('Monday',0,'Tamil')})
        with self.assertRaisesRegex(TimetableError,'Teacher conflicts'):
            self.db.save(b,{('Monday',0):Slot('Monday',0,'English')})
        self.assertFalse(self.db.load(b))
        self.assertFalse(overlap('8',3,'9',0))
        self.assertTrue(overlap('8',0,'9',0))
    def test_edited_teacher_overrides(self):
        a=CTX;b=(*CTX[:3],'9','A')
        self.db.save(a,{('Monday',0):Slot('Monday',0,'Tamil',teacher='Override')})
        with self.assertRaises(TimetableError):
            self.db.save(b,{('Monday',0):Slot('Monday',0,'Physics',teacher='Override')})
    def test_custom_sections_and_rules_persist(self):
        self.db.add_section('8','D')
        self.db.set_subjects('8','D',['Tamil','Maths'],['PET'])
        self.assertEqual(self.db.subjects('8','D'),(['Tamil','Maths'],['PET']))
        self.db.delete_section('8','D')
        self.db.close();self.db=Store(self.folder/'test.sqlite3')
        self.assertNotIn('D',self.db.sections('8'))
    def test_exports(self):
        slots=Scheduler(self.db).generate(CTX)
        for name,fn in [('test.docx',export_word),('test.xlsx',export_excel),('test.pdf',export_pdf)]:
            path=self.folder/name
            fn(str(path),CTX,slots,self.db)
            self.assertGreater(path.stat().st_size,1000)
    def test_backup(self):
        self.db.save(CTX,Scheduler(self.db).generate(CTX))
        dest=self.folder/'copy.sqlite3'
        self.db.backup(dest)
        copy=Store(dest)
        self.assertEqual(len(copy.load(CTX)),35)
        copy.close()
    def test_generation_with_blocked_teacher(self):
        self.db.assign('8','A','SKKA','Shared')
        self.db.assign('6','A','SKKA','Shared')
        self.db.save((*CTX[:3],'6','A'),{('Friday',5):Slot('Friday',5,'SKKA')})
        with self.assertRaisesRegex(TimetableError,'fixed school activity'):
            Scheduler(self.db).generate(CTX)

if __name__=='__main__':unittest.main()
