"""Date-based morning-test planner; no period grid and no internet requirement.

School-specific holidays must be entered by an administrator. SQLite is stored
alongside the class timetable database and is backed up with it.
"""
from __future__ import annotations
from datetime import date, timedelta
import random
import re
from timetable_core import TimetableError

CYCLES = ('Edification 1', 'Edification 2', 'Edification 3')
TEST_TIME = '08:00–08:45'


def parse_date(value):
    try:
        result = date.fromisoformat(str(value).strip())
        if result.isoformat() != str(value).strip():
            raise ValueError
        return result
    except ValueError as exc:
        raise TimetableError('Enter a valid date as YYYY-MM-DD, e.g. 2026-11-02.') from exc


class Edification:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS edification_holidays (
                day TEXT PRIMARY KEY, reason TEXT NOT NULL)''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS edification_tests (
                grade TEXT NOT NULL, section TEXT NOT NULL, cycle TEXT NOT NULL,
                position INTEGER NOT NULL, day TEXT NOT NULL, subject TEXT NOT NULL,
                PRIMARY KEY (grade,section,cycle,position))''')
            self.db.execute('CREATE INDEX IF NOT EXISTS idx_edification_day ON edification_tests(day)')
            self.db.execute('''CREATE TABLE IF NOT EXISTS edification_subjects (
                grade TEXT NOT NULL, section TEXT NOT NULL, position INTEGER NOT NULL,
                subject TEXT NOT NULL, PRIMARY KEY(grade,section,position))''')

    def subject_bank(self, grade, section):
        """Return a saved main-subject list, or initial class subject defaults."""
        rows = self.db.execute('''SELECT subject FROM edification_subjects
            WHERE grade=? AND section=? ORDER BY position''',(grade,section)).fetchall()
        if rows:
            return [row['subject'] for row in rows]
        mains, _ = self.store.subjects(grade, section)
        return list(dict.fromkeys(mains))

    def save_subject_bank(self, grade, section, subjects):
        if grade not in ('6','7','8','9','10','11','12') or section not in self.store.sections(grade):
            raise TimetableError('Select a valid class and section.')
        clean = [str(subject).strip() for subject in subjects]
        if not 1 <= len(clean) <= 15 or any(not s or len(s)>65 for s in clean):
            raise TimetableError('Choose 1–15 main subjects (each up to 65 characters).')
        if len({s.casefold() for s in clean}) != len(clean):
            raise TimetableError('Duplicate subjects are not allowed.')
        with self.db:
            self.db.execute('DELETE FROM edification_subjects WHERE grade=? AND section=?',
                            (grade,section))
            self.db.executemany('INSERT INTO edification_subjects VALUES(?,?,?,?)',
                                [(grade,section,i,s) for i,s in enumerate(clean,1)])

    def holidays(self, year=None, month=None):
        rows=self.db.execute('SELECT day,reason FROM edification_holidays ORDER BY day').fetchall()
        return {row['day']:row['reason'] for row in rows if
                (year is None or int(row['day'][:4])==year) and
                (month is None or int(row['day'][5:7])==month)}

    def set_holiday(self, day, reason):
        d=parse_date(day)
        reason=str(reason).strip()
        if not reason or len(reason)>120:raise TimetableError('Enter a holiday reason (1–120 characters).')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO edification_holidays VALUES(?,?)',(d.isoformat(),reason))

    def delete_holiday(self, day):
        with self.db:self.db.execute('DELETE FROM edification_holidays WHERE day=?',(parse_date(day).isoformat(),))

    def is_available(self, day):
        d=parse_date(day) if isinstance(day,str) else day
        return d.weekday()!=6 and d.isoformat() not in self.holidays(d.year,d.month)

    def next_day(self, start):
        d=parse_date(start) if isinstance(start,str) else start
        for _ in range(366*3):
            if self.is_available(d):return d
            d+=timedelta(days=1)
        raise TimetableError('No available school date found in the next three years.')

    def tests(self, grade, section, cycle):
        return self.db.execute('''SELECT position,day,subject FROM edification_tests
            WHERE grade=? AND section=? AND cycle=? ORDER BY position''',
            (grade,section,cycle)).fetchall()

    def _varied_order(self, grade, section, cycle, subjects):
        """Vary test order between cycles, without losing or duplicating subjects.

        Date allocation is separate: holidays and existing cycle dates still apply.
        For a single subject a different order is mathematically impossible.
        """
        originals = list(subjects)
        if len(originals) < 2:
            return originals
        history = {}
        for name in CYCLES:
            if name != cycle:
                previous = [row['subject'] for row in self.tests(grade, section, name)]
                if len(previous) == len(originals) and {s.casefold() for s in previous} == {
                        s.casefold() for s in originals}:
                    history[name] = previous
        # Give the preceding cycle priority when the subject sets match.
        index = CYCLES.index(cycle)
        prior = next((history[name] for name in reversed(CYCLES[:index]) if name in history), None)
        rng = random.SystemRandom()
        best, best_score = None, None
        for _ in range(80):
            candidate = originals[:]
            rng.shuffle(candidate)
            if candidate == originals and len(originals) > 1:
                continue
            difference = sum(a.casefold() != b.casefold()
                             for a, b in zip(candidate, prior)) if prior else 0
            distinct = sum(candidate != other for other in history.values())
            # Reward changes in position and avoid exact repeats across cycles.
            score = (difference, distinct, sum(a != b for a,b in zip(candidate, originals)))
            if best_score is None or score > best_score:
                best, best_score = candidate, score
            if prior and difference == len(candidate) and distinct == len(history):
                break
        if best is None:
            best = originals[1:] + originals[:1]
        if prior and best == prior:
            # Guaranteed different ordering, even with just two subjects.
            best = best[1:] + best[:1]
        return best

    def delete_cycle(self, grade, section, cycle):
        """Remove one selected timetable, leaving other cycles and subjects intact."""
        if cycle not in CYCLES:
            raise TimetableError('Choose Edification 1, 2 or 3.')
        with self.db:
            result = self.db.execute("""DELETE FROM edification_tests
                WHERE grade=? AND section=? AND cycle=?""", (grade, section, cycle))
        return result.rowcount

    def shuffle_cycle(self, grade, section, cycle):
        """Reshuffle subjects but preserve the dates, days and holiday skips."""
        rows = self.tests(grade, section, cycle)
        if len(rows) < 2:
            raise TimetableError('Add at least two tests before shuffling.')
        current = [row['subject'] for row in rows]
        reordered = self._varied_order(grade, section, cycle, current)
        if reordered == current:
            reordered = current[1:] + current[:1]
        with self.db:
            for row, subject in zip(rows, reordered):
                self.db.execute("""UPDATE edification_tests SET subject=?
                    WHERE grade=? AND section=? AND cycle=? AND position=?""",
                    (subject, grade, section, cycle, row['position']))
        return reordered

    def swap_cycle_subjects(self, grade, section, cycle, first, second):
        """Swap two test subjects without changing dates or duplicating subjects."""
        rows = {row['position']: row for row in self.tests(grade, section, cycle)}
        if first == second or first not in rows or second not in rows:
            raise TimetableError('Choose two different existing test numbers.')
        with self.db:
            for pos, subject in ((first, rows[second]['subject']),
                                 (second, rows[first]['subject'])):
                self.db.execute("""UPDATE edification_tests SET subject=?
                    WHERE grade=? AND section=? AND cycle=? AND position=?""",
                    (subject, grade, section, cycle, pos))

    def generate(self, grade, section, cycle, start_date, subjects, *, replace=False):
        if cycle not in CYCLES:raise TimetableError('Select Edification 1, 2 or 3.')
        if grade not in ('6','7','8','9','10','11','12'):
            raise TimetableError('Edification tests are configured for classes 6–12.')
        if section not in self.store.sections(grade):raise TimetableError('Select a valid section.')
        subjects=[str(s).strip() for s in subjects]
        if not subjects or len(subjects)>15 or any(not s or len(s)>65 for s in subjects):
            raise TimetableError('Choose 1–15 valid subjects.')
        if len({s.casefold() for s in subjects})!=len(subjects):
            raise TimetableError('Subjects must not repeat within a cycle.')
        if self.tests(grade,section,cycle) and not replace:
            raise TimetableError('This cycle already exists. Confirm replacement to regenerate it.')
        start=parse_date(start_date)
        # The subject bank remains in the user's order; each test cycle is varied.
        subjects=self._varied_order(grade, section, cycle, subjects)
        # Avoid silently scheduling two cycles for one class/section on one date.
        occupied={row[0] for row in self.db.execute('''SELECT day FROM edification_tests
            WHERE grade=? AND section=? AND cycle<>?''',(grade,section,cycle))}
        proposed=[]
        day=start
        for pos,subject in enumerate(subjects,1):
            for _ in range(366*3):
                day=self.next_day(day)
                if day.isoformat() not in occupied:break
                day+=timedelta(days=1)
            else:raise TimetableError('Could not find an available test date.')
            proposed.append((grade,section,cycle,pos,day.isoformat(),subject))
            day+=timedelta(days=1)
        with self.db:
            self.db.execute('DELETE FROM edification_tests WHERE grade=? AND section=? AND cycle=?',
                            (grade,section,cycle))
            self.db.executemany('INSERT INTO edification_tests VALUES(?,?,?,?,?,?)',proposed)
        return proposed

    def update_test(self, grade, section, cycle, position, new_date, subject):
        day=parse_date(new_date)
        subject=str(subject).strip()
        if not subject or len(subject)>65:raise TimetableError('Enter a valid subject.')
        if not self.is_available(day):raise TimetableError('The selected date is Sunday or a recorded holiday.')
        old=self.db.execute('''SELECT subject FROM edification_tests
            WHERE grade=? AND section=? AND cycle=? AND position=?''',
            (grade,section,cycle,position)).fetchone()
        if not old:raise TimetableError('Test entry not found.')
        duplicate_subject=self.db.execute('''SELECT 1 FROM edification_tests
            WHERE grade=? AND section=? AND cycle=? AND position<>?
              AND LOWER(subject)=LOWER(?) LIMIT 1''',
            (grade,section,cycle,position,subject)).fetchone()
        if duplicate_subject:
            raise TimetableError('This subject is already in the timetable. Use Swap Order instead.')
        collision=self.db.execute('''SELECT 1 FROM edification_tests WHERE grade=? AND section=?
            AND day=? AND NOT (cycle=? AND position=?)''',
            (grade,section,day.isoformat(),cycle,position)).fetchone()
        if collision:raise TimetableError('Another test for this section is already on that date.')
        with self.db:self.db.execute('''UPDATE edification_tests SET day=?,subject=?
            WHERE grade=? AND section=? AND cycle=? AND position=?''',
            (day.isoformat(),subject,grade,section,cycle,position))

    def delete_test(self,grade,section,cycle,position):
        with self.db:self.db.execute('''DELETE FROM edification_tests WHERE grade=? AND section=?
            AND cycle=? AND position=?''',(grade,section,cycle,position))

    def rename_section(self,grade,old,new):
        with self.db:
            self.db.execute('UPDATE edification_tests SET section=? WHERE grade=? AND section=?',(new,grade,old))
            self.db.execute('UPDATE edification_subjects SET section=? WHERE grade=? AND section=?',(new,grade,old))

    def delete_section(self,grade,section):
        with self.db:
            self.db.execute('DELETE FROM edification_tests WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM edification_subjects WHERE grade=? AND section=?',(grade,section))
