"""Run: python -m unittest -v test_timetable.py test_unit_dashboard.py"""
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from timetable_core import (
    GRADES, UNIT_GRADES, Store, Scheduler, Slot, TimetableError,
    unit_for_grade, weekly_capacity, school_days, PERIODS, group
)

CONTEXT=('2026-2027','October','Week 1','8','A')

class UnitDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'unit.sqlite3'
        self.store=Store(self.path)
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_unit_class_mapping(self):
        self.assertEqual(UNIT_GRADES['Unit 1'],('LKG','UKG','1'))
        self.assertEqual(UNIT_GRADES['Unit 2'],('2','3','4','5'))
        self.assertEqual(UNIT_GRADES['Unit 3'],('6','7','8'))
        self.assertEqual(UNIT_GRADES['Unit 4'],('9','10'))
        self.assertEqual(UNIT_GRADES['Unit 5'],('11','12'))
        self.assertEqual({grade for grades in UNIT_GRADES.values() for grade in grades},set(GRADES))
        self.assertEqual(unit_for_grade('12'),'Unit 5')

    def test_exact_periods_and_persistence(self):
        plan={'Tamil':5,'English':5,'Maths':5,'Science':5,'Social Science':5,'PET':4,'SKKA':2,'STEM':4}
        self.assertEqual(sum(plan.values()),35)
        self.store.save_weekly_plan('8','A',plan)
        schedule=Scheduler(self.store).generate(CONTEXT)
        actual=Counter(slot.subject for slot in schedule.values())
        self.assertEqual(dict(actual),plan)
        self.assertEqual(schedule[('Friday',5)].subject,'SKKA')
        self.assertEqual(schedule[('Friday',6)].subject,'SKKA')
        self.store.save(CONTEXT,schedule)
        self.store.close()
        self.store=Store(self.path)
        self.assertEqual(self.store.weekly_plan('8','A'),plan)
        self.assertEqual(self.store.load(CONTEXT),schedule)

    def test_add_remove_subjects_and_unallocated_slots(self):
        self.store.save_weekly_plan('8','A',{'Tamil':5,'Maths':4,'PET':2})
        timetable=Scheduler(self.store).generate(CONTEXT)
        counts=Counter(s.subject for s in timetable.values())
        self.assertEqual(counts['Tamil'],5)
        self.assertEqual(counts['Maths'],4)
        self.assertEqual(counts['PET'],2)
        self.assertEqual(counts['---'],24)
        self.assertNotIn('English',counts)
        self.store.save(CONTEXT,timetable)

    def test_excess_periods_rejected_without_changing_saved_plan(self):
        self.store.save_weekly_plan('8','A',{'Tamil':5})
        with self.assertRaisesRegex(TimetableError,'only 35'):
            self.store.save_weekly_plan('8','A',{'Tamil':25,'English':20})
        self.assertEqual(self.store.weekly_plan('8','A'),{'Tamil':5})

    def test_manual_edit_cannot_break_exact_quota(self):
        self.store.save_weekly_plan('8','A',{'Tamil':5,'Maths':5})
        schedule=Scheduler(self.store).generate(CONTEXT)
        slot=next(s for s in schedule.values() if s.subject=='Tamil')
        schedule[(slot.day,slot.period)]=Slot(slot.day,slot.period,'Maths')
        with self.assertRaisesRegex(TimetableError,'Weekly subject periods'):
            self.store.save(CONTEXT,schedule)

    def test_removed_skka_is_not_forced(self):
        self.store.save_weekly_plan('8','A',{'Tamil':5,'Maths':5,'PET':5})
        schedule=Scheduler(self.store).generate(CONTEXT)
        self.assertFalse(any(s.subject=='SKKA' for s in schedule.values()))

    def test_teacher_blocked_every_slot_rejected(self):
        other=(*CONTEXT[:3],'6','A')
        other_slots={(d,p):Slot(d,p,'Class',teacher='Shared')
                     for d in school_days('6') for p in range(len(PERIODS[group('6')]))}
        self.store.save(other,other_slots)
        self.store.assign('8','A','Tamil','Shared')
        self.store.save_weekly_plan('8','A',{'Tamil':5})
        with self.assertRaisesRegex(TimetableError,'Cannot fit'):
            Scheduler(self.store).generate(CONTEXT)

    def test_plan_works_for_every_class(self):
        for grade in GRADES:
            context=(*CONTEXT[:3],grade,'A')
            plan={'Tamil':5,'English':4,'Maths':3}
            self.store.save_weekly_plan(grade,'A',plan)
            schedule=Scheduler(self.store).generate(context)
            self.assertEqual(len(schedule),weekly_capacity(grade))
            count=Counter(s.subject for s in schedule.values())
            self.assertEqual([count['Tamil'],count['English'],count['Maths']],[5,4,3])
            self.store.save(context,schedule)

if __name__=='__main__':unittest.main()
