"""Regression tests for automatic scheduling and smart shuffle."""
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from timetable_core import Store, Scheduler, TimetableError, school_days, PERIODS, group

CTX = ('2026-2027','October','Week 1','12','B1')
PLAN = {'Chemistry':6,'Computer':4,'English':6,'Maths':6,'PET':2,'Physics':6,'Tamil':6}

class SmartAutoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'test.sqlite3')
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_subjects_vary_by_day_and_preserve_exact_counts(self):
        self.store.save_weekly_plan('12','B1',PLAN)
        schedule=Scheduler(self.store).generate(CTX)
        self.assertEqual(Counter(s.subject for s in schedule.values()),Counter(PLAN))
        days=school_days('12')
        self.assertGreaterEqual(len({schedule[(day,0)].subject for day in days}),4)
        self.assertEqual(len({tuple(schedule[(day,p)].subject for p in range(6)) for day in days}),6)

    def test_reshuffle_is_different_and_valid(self):
        self.store.save_weekly_plan('12','B1',PLAN)
        a=Scheduler(self.store).generate(CTX,variation=1)
        self.store.save(CTX,a)
        b=Scheduler(self.store).generate(CTX,variation=2026)
        self.assertNotEqual(a,b)
        self.store.save(CTX,b)
        self.assertEqual(Counter(s.subject for s in b.values()),Counter(PLAN))

    def test_atomic_plan_update_rebuilds_all_weeks(self):
        self.store.save_weekly_plan('12','B1',PLAN)
        scheduler=Scheduler(self.store)
        self.store.save(CTX,scheduler.generate(CTX))
        next_week=(*CTX[:2],'Week 2',*CTX[3:])
        self.store.save(next_week,scheduler.generate(next_week))
        new_plan=dict(PLAN)
        new_plan['Computer']=3
        new_plan['PET']=3
        count=self.store.apply_plan_and_regenerate('12',['B1'],new_plan,CTX,variation=101)
        self.assertEqual(count,2)
        for ctx in (CTX,next_week):
            self.assertEqual(Counter(s.subject for s in self.store.load(ctx).values()),Counter(new_plan))

    def test_atomic_failure_keeps_previous_plan_and_timetable(self):
        self.store.save_weekly_plan('12','B1',PLAN)
        previous=Scheduler(self.store).generate(CTX)
        self.store.save(CTX,previous)
        other=(*CTX[:3],'12','A')
        from timetable_core import Slot
        # Block every available slot for a teacher shared with a target subject.
        self.store.save(other,{(day,p):Slot(day,p,'Block',teacher='Shared')
                               for day in school_days('12') for p in range(len(PERIODS[group('12')]))})
        self.store.assign('12','B1','PET','Shared')
        with self.assertRaises(TimetableError):
            self.store.apply_plan_and_regenerate('12',['B1'],dict(PLAN),CTX,variation=102)
        self.assertEqual(self.store.weekly_plan('12','B1'),PLAN)
        self.assertEqual(self.store.load(CTX),previous)

    def test_other_classes_unchanged(self):
        other=(*CTX[:3],'9','A')
        self.store.save(other,Scheduler(self.store).generate(other))
        old=self.store.load(other)
        self.store.apply_plan_and_regenerate('12',['B1'],PLAN,CTX,variation=33)
        self.assertEqual(self.store.load(other),old)

    def test_middle_fixed_skka_stays(self):
        context=(*CTX[:3],'8','A')
        plan={'Tamil':5,'English':5,'Maths':5,'Science':5,'Social Science':5,'PET':4,'SKKA':2,'STEM':4}
        self.store.apply_plan_and_regenerate('8',['A'],plan,context,variation=13)
        saved=self.store.load(context)
        self.assertEqual(saved[('Friday',5)].subject,'SKKA')
        self.assertEqual(saved[('Friday',6)].subject,'SKKA')
        self.assertEqual(Counter(s.subject for s in saved.values()),Counter(plan))

if __name__=='__main__':
    unittest.main()
