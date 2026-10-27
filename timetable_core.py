"""Core scheduling and durable storage for APMHSS Timetable Generator.

Python 3.10+. Only standard library required for storage and timetable logic.
The scheduler *detects* known teacher conflicts and refuses impossible requests;
assignment data must be entered to make clash checks meaningful.
"""
from __future__ import annotations
import calendar
import hashlib
import json
import sys
import hmac
import os
import random
import heapq
import re
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHOOL = 'ADHIPARASAKTHI MATRICULATION HIGHER SECONDARY SCHOOL'
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
GRADES = ('Pre KG', 'LKG', 'UKG', *map(str, range(1, 13)))
UNIT_GRADES = {
    'Unit 1 (KG–2)': ('Pre KG', 'LKG', 'UKG', '1', '2'),
    'Unit 2 (3–5)': ('3', '4', '5'),
    'Unit 3 (6–8)': ('6', '7', '8'),
    'Unit 4 (9–12)': ('9', '10', '11', '12'),
}

def unit_for_grade(grade):
    return next((unit for unit, grades in UNIT_GRADES.items() if grade in grades), 'Unit 1')

def weekly_capacity(grade):
    return len(school_days(grade)) * len(CLASS_PERIODS.get(grade, PERIODS[group(grade)]))

DEFAULT_SECTIONS = {g: (('A', 'B', 'C1', 'C2') if g in ('11', '12') else ('A', 'B', 'C')) for g in GRADES}

# Original project's grade-specific subject choices and special activities.
SENIOR_SUBJECTS = {
    'A': ['Tamil','English','Maths','Physics','Chemistry','Biology'],
    'B1': ['Tamil','English','Maths','Physics','Chemistry','Comp. Sci'],
    'B2': ['Tamil','English','Physics','Chemistry','Comp. Sci','Biology'],
    'B': ['Tamil','English','Physics','Chemistry','Comp. Sci','Maths / Biology'],
    'C1': ['Tamil','English','Bus. Maths','Economics','Accounts','Commerce'],
    'C2': ['Tamil','English','Economics','Accounts','Comp. App','Commerce'],
}

# Period indices, title, times. The source document uses 12-hour times without AM/PM;
# all afternoon values 01:00-04:30 are interpreted as 13:00-16:30.
PERIODS = {
    'primary': [('08:45','09:30'),('09:30','10:10'),('10:10','10:50'),('11:00','11:45'),('11:45','12:30'),('01:00','01:40'),('01:40','02:20'),('02:30','03:30')],
    'middle': [('09:00','09:45'),('09:45','10:30'),('10:45','11:40'),('11:40','12:50'),('01:20','02:00'),('02:00','02:40'),('02:50','03:10')],
    'higher': [('08:45','09:30'),('09:30','10:50'),('11:00','12:30'),('01:00','02:20'),('02:30','03:30'),('03:30','04:30')],
}

# New CLASS timings only. Revision retains the historical PERIODS above.
# Timings are editable per section in the existing Period Timing / Layout screen.
CLASS_PERIODS = {
    **{g: PERIODS['primary'] for g in ('Pre KG','LKG','UKG','1','2','3','4','5')},
    **{g: [
        ('09:00','09:40'),('09:40','10:20'),
        ('10:30','11:15'),('11:15','12:00'),
        ('12:35','13:15'),('13:15','13:55'),
        ('14:05','14:45'),('14:45','15:30')]
       for g in ('6','7','8')},
    '9': [
        ('09:00','09:40'),('09:40','10:20'),
        ('10:35','11:15'),('11:15','11:55'),
        ('12:55','13:35'),('13:35','14:15'),
        ('14:30','15:10'),('15:10','15:50'),('15:50','16:30')],
    **{g: [
        ('08:45','09:30'),('09:30','10:15'),
        ('10:25','11:10'),('11:10','11:55'),('11:55','12:40'),
        ('13:20','14:05'),('14:05','14:50'),
        ('15:00','15:45'),('15:45','16:30')]
       for g in ('10','11','12')},
}

SENIOR_LABELS = {'A':'Biology','B':'Computer–Maths / Biology','B1':'Computer–Maths','B2':'Computer–Biology',
                 'C1':'BM','C2':'CA'}

def section_display(grade, section):
    if grade in ('11','12'):
        return f'{section} - {SENIOR_LABELS[section]}' if section in SENIOR_LABELS else section
    return section


def group(grade):
    return 'middle' if grade in ('6','7','8') else 'higher' if grade in ('9','10','11','12') else 'primary'

def school_days(grade):
    return DAYS[:5] if group(grade) == 'middle' else DAYS

def minutes(clock):
    h, m = (int(x) for x in clock.split(':'))
    if h in (1,2,3,4,5,6,7): h += 12
    return h*60+m

def interval(grade, period):
    a,b = PERIODS[group(grade)][period]
    return minutes(a), minutes(b)

def overlap(grade_a, period_a, grade_b, period_b):
    start_a, end_a = interval(grade_a,period_a)
    start_b, end_b = interval(grade_b,period_b)
    return start_a < end_b and start_b < end_a

def default_subjects(grade, section):
    if group(grade) == 'primary':
        return ['Tamil','English','Maths','EVS','Comp. Sci'], ['Sakthi Neri','French','Hindi','PET','PET']
    if group(grade) == 'middle':
        return ['Tamil','English','Maths','Science','Social Sci'], ['Comp. Sci','Comp. Sci','PET','PET','STEM','Sakthi Neri','SKKA','French','Hindi']
    if grade == '9':
        return ['Tamil','English','Maths','Physics','Biology','Social Sci','Comp. Sci'], ['Sakthi Neri','French','STEM','Hindi']
    if grade == '10':
        return ['Tamil','English','Maths','Science','Social'], []
    return list(SENIOR_SUBJECTS.get(section.split(' - ')[0], SENIOR_SUBJECTS['A'])), ([] if grade=='12' else ['Sakthi Neri'])

def teacher_set(value):
    """For a split Maths/Biology lesson, check BOTH teachers for clashes."""
    return {name.strip().casefold() for name in str(value).split(' / ') if name.strip()}

def strong_password(password):
    return len(password) >= 8 and any(c.isalpha() for c in password) and any(c.isdigit() for c in password)

class TimetableError(ValueError):
    pass

@dataclass(frozen=True)
class Slot:
    day: str
    period: int
    subject: str
    teacher: str = ''
    bg: str = '#FFFFFF'
    fg: str = '#243043'

class Store:
    def __init__(self, path=None, kind='class'):
        self.kind = kind
        # A one-file EXE can contain a READ-ONLY snapshot of the database.
        # SQLite requires a WRITABLE database while the application runs.
        # On the first run, extract the bundled snapshot to LOCALAPPDATA.
        # The database never needs to be carried as a separate file when
        # transferring the EXE that was built with the latest snapshot.
        frozen = bool(getattr(sys, 'frozen', False))
        app_dir = (Path(sys.executable).resolve().parent if frozen
                   else Path(__file__).resolve().parent)
        local_data = (Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
                      / 'APMHSS_Time_Table' / 'school_timetable.sqlite3')
        # Portable package: database stays next to the executable and moves
        # automatically when the WHOLE PORTABLE_APP folder is copied.
        # This is a real writable SQLite file, not an embedded EXE database.
        portable = (os.environ.get('APMHSS_PORTABLE') == '1' or
                    (app_dir / '.APMHSS_PORTABLE').is_file())
        portable_location = app_dir / 'school_timetable.sqlite3'
        default_location = (portable_location if portable else
                            local_data if frozen else app_dir / 'data' / 'school_timetable.sqlite3')
        location = path or os.environ.get('APMHSS_DB_PATH') or default_location
        self.path = Path(location).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if path is None and not os.environ.get('APMHSS_DB_PATH') and not self.path.exists():
            # Preserve any older installation's existing data before trying
            # the bundled snapshot. Explicit DB paths are never overwritten.
            bundled_root = Path(getattr(sys, '_MEIPASS', app_dir))
            candidates = [
                app_dir / 'data' / 'school_timetable.sqlite3',
                Path.home() / '.apmhss_timetable' / 'school_timetable.sqlite3',
                bundled_root / 'data' / 'school_timetable.sqlite3',
            ]
            for source_path in candidates:
                if not source_path.is_file() or source_path.resolve() == self.path:
                    continue
                try:
                    with sqlite3.connect(str(source_path)) as source:
                        def columns(table):
                            return {row[1] for row in source.execute(f'PRAGMA table_info({table})')}
                        compatible = (
                            {'year','month','week','grade','section','day','period','subject'}
                            <= columns('schedules')
                            and {'username','salt'} <= columns('users')
                            and bool({'password_hash','digest'} & columns('users'))
                        )
                        if not compatible:
                            continue
                        with sqlite3.connect(str(self.path)) as destination:
                            source.backup(destination)
                    break
                except sqlite3.DatabaseError:
                    # Never replace an incompatible legacy DB or a corrupt file.
                    continue
        self.db = sqlite3.connect(str(self.path), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=15000')
        self.db.execute('PRAGMA foreign_keys=ON')
        self._schema()
        if self.kind=='class':
            self._migrate_senior_b()
            self._import_pdf_reference()

    def _schema(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')));
        CREATE TABLE IF NOT EXISTS sections (grade TEXT NOT NULL, section TEXT NOT NULL,
          PRIMARY KEY(grade,section));
        CREATE TABLE IF NOT EXISTS assignments (
          grade TEXT NOT NULL, section TEXT NOT NULL, subject TEXT NOT NULL,
          teacher TEXT NOT NULL, PRIMARY KEY(grade,section,subject));
        CREATE TABLE IF NOT EXISTS rules (
          grade TEXT NOT NULL, section TEXT NOT NULL, main TEXT NOT NULL, extras TEXT NOT NULL,
          PRIMARY KEY(grade,section));
        CREATE TABLE IF NOT EXISTS schedules (
          year TEXT NOT NULL, month TEXT NOT NULL, week TEXT NOT NULL,
          grade TEXT NOT NULL, section TEXT NOT NULL, day TEXT NOT NULL,
          period INTEGER NOT NULL, subject TEXT NOT NULL, teacher_override TEXT NOT NULL DEFAULT '',
          bg TEXT NOT NULL DEFAULT '#FFFFFF', fg TEXT NOT NULL DEFAULT '#243043',
          PRIMARY KEY(year,month,week,grade,section,day,period));
        CREATE TABLE IF NOT EXISTS weekly_subject_periods (
          grade TEXT NOT NULL, section TEXT NOT NULL, subject TEXT NOT NULL,
          periods INTEGER NOT NULL CHECK(periods > 0),
          PRIMARY KEY(grade,section,subject));
        CREATE TABLE IF NOT EXISTS timetable_layout (
          grade TEXT NOT NULL, section TEXT NOT NULL,
          day_count INTEGER NOT NULL, period_times TEXT NOT NULL,
          PRIMARY KEY(grade,section));
        CREATE TABLE IF NOT EXISTS weekly_plan_enabled (
          grade TEXT NOT NULL, section TEXT NOT NULL, PRIMARY KEY(grade,section));
        CREATE TABLE IF NOT EXISTS student_counts (
          grade TEXT NOT NULL, section TEXT NOT NULL, students INTEGER NOT NULL,
          PRIMARY KEY(grade,section));
        CREATE TABLE IF NOT EXISTS subject_access (
          username TEXT NOT NULL, grade TEXT NOT NULL, section TEXT NOT NULL,
          subject TEXT NOT NULL, PRIMARY KEY(username,grade,section,subject));
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          username TEXT NOT NULL, event TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pdf_reference_pages (
          page INTEGER PRIMARY KEY, grade TEXT NOT NULL, section TEXT NOT NULL,
          original_class TEXT NOT NULL, page_text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pdf_reference_teachers (
          page INTEGER NOT NULL, grade TEXT NOT NULL, section TEXT NOT NULL,
          teacher TEXT NOT NULL, subject TEXT NOT NULL,
          PRIMARY KEY(page,grade,section,teacher,subject));
        CREATE TABLE IF NOT EXISTS teacher_profiles (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        ''')
        # Upgrade old user tables without deleting stored accounts.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(users)')}
        legacy_digest = 'digest' in columns and 'password_hash' not in columns
        with self.db:
            if 'password_hash' not in columns and 'digest' in columns:
                self.db.execute('ALTER TABLE users ADD COLUMN password_hash BLOB')
                self.db.execute('UPDATE users SET password_hash=digest')
            if 'role' not in columns:
                self.db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
            if 'kdf_iterations' not in columns:
                self.db.execute('ALTER TABLE users ADD COLUMN kdf_iterations INTEGER NOT NULL DEFAULT 310000')
                if legacy_digest:
                    self.db.execute('UPDATE users SET kdf_iterations=250000')
        with self.db:
            for senior_grade in ('11','12'):
                self.db.execute('INSERT OR IGNORE INTO sections(grade,section) VALUES(?,?)',(senior_grade,'B'))
            if not self.db.execute("SELECT 1 FROM settings WHERE key='sections_seeded'").fetchone():
                for grade, sections in DEFAULT_SECTIONS.items():
                    for sec in sections:
                        self.db.execute('INSERT OR IGNORE INTO sections VALUES(?,?)',(grade,sec))
                self.db.execute("INSERT INTO settings VALUES('sections_seeded','yes')")

    def _migrate_senior_b(self):
        """Non-destructively copy legacy B1/B2 into one B stream, keeping originals."""
        if self.db.execute("SELECT 1 FROM settings WHERE key='senior_b_merged_2026'").fetchone():return
        with self.db:
            for grade in ('11','12'):
                for old in ('B1','B2'):
                    self.db.execute('''INSERT OR IGNORE INTO schedules
                        (year,month,week,grade,section,day,period,subject,teacher_override,bg,fg)
                        SELECT year,month,week,grade,'B',day,period,subject,teacher_override,bg,fg
                        FROM schedules WHERE grade=? AND section=?''',(grade,old))
                self.db.execute('''UPDATE schedules SET subject='Maths / Biology'
                    WHERE grade=? AND section='B' AND lower(subject) IN ('maths','biology')''',(grade,))
                self.db.execute('''INSERT OR IGNORE INTO student_counts(grade,section,students)
                    SELECT grade,'B',students FROM student_counts WHERE grade=? AND section='B1' ''',(grade,))
                self.db.execute('''INSERT OR IGNORE INTO subject_access(username,grade,section,subject)
                    SELECT username,grade,'B',CASE WHEN lower(subject) IN ('maths','biology')
                    THEN 'Maths / Biology' ELSE subject END FROM subject_access
                    WHERE grade=? AND section IN ('B1','B2')''',(grade,))
                self.db.execute('''INSERT OR IGNORE INTO assignments(grade,section,subject,teacher)
                    SELECT grade,'B',subject,teacher FROM assignments
                    WHERE grade=? AND section='B1' AND lower(subject) NOT IN ('maths','biology')''',(grade,))
                maths=self.db.execute("SELECT teacher FROM assignments WHERE grade=? AND section='B1' AND lower(subject)='maths'",(grade,)).fetchone()
                bio=self.db.execute("SELECT teacher FROM assignments WHERE grade=? AND section='B2' AND lower(subject)='biology'",(grade,)).fetchone()
                combined=' / '.join(dict.fromkeys([x[0] for x in (maths,bio) if x and x[0]]))
                if combined:
                    self.db.execute("INSERT OR IGNORE INTO assignments(grade,section,subject,teacher) VALUES(?,'B','Maths / Biology',?)",(grade,combined))
            self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('senior_b_merged_2026','yes')")

    def _import_pdf_reference(self):
        """Import 65-page PDF reference text and teacher/subject candidates once.

        Teacher candidates are stored for review, not silently approved for
        live scheduling; incorrect assignments could create false clashes.
        """
        if self.db.execute("SELECT 1 FROM settings WHERE key='pdf_reference_imported_2026'").fetchone():
            return
        bundled=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
        seed=bundled/'reference_seed.json'
        if not seed.is_file():return
        try:
            with seed.open(encoding='utf-8') as f:payload=json.load(f)
            with self.db:
                for item in payload['pages']:
                    self.db.execute('INSERT OR IGNORE INTO pdf_reference_pages (page,grade,section,original_class,page_text) VALUES(?,?,?,?,?)',
                      (item['page'],item['grade'],item['section'],item['original_class'],item['text']))
                    grade,section=item['grade'],item['section']
                    if grade in GRADES and section and (grade not in ('11','12') or section in ('A','B','C1','C2')):
                        self.db.execute('INSERT OR IGNORE INTO sections(grade,section) VALUES(?,?)',(grade,section))
                for item in payload['teachers']:
                    self.db.execute('INSERT OR IGNORE INTO pdf_reference_teachers (page,grade,section,teacher,subject) VALUES(?,?,?,?,?)',
                      (item['page'],item['grade'],item['section'],item['teacher'],item['subject']))
                self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pdf_reference_imported_2026','yes')")
        except (OSError,ValueError,sqlite3.DatabaseError,KeyError) as error:
            print('PDF reference import skipped:',error)

    def pdf_teacher_choices(self,grade,section):
        rows=self.db.execute('SELECT DISTINCT teacher,subject FROM pdf_reference_teachers WHERE grade=? AND section=? ORDER BY subject,teacher',
                             (grade,section)).fetchall()
        return [dict(r) for r in rows]

    def close(self): self.db.close()
    def layout(self, grade, section):
        """(day names, period times), with optional per-class-and-section overrides."""
        row = self.db.execute('SELECT day_count,period_times FROM timetable_layout WHERE grade=? AND section=?',
                              (grade, section)).fetchone()
        if row:
            return DAYS[:row['day_count']], [tuple(t) for t in json.loads(row['period_times'])]
        # Convert old 12-hour labels to unambiguous 24-hour labels.
        original = (PERIODS[group(grade)] if self.kind=='revision'
                    else CLASS_PERIODS.get(grade, PERIODS[group(grade)]))
        return school_days(grade), [(f'{minutes(a)//60:02d}:{minutes(a)%60:02d}',
                                     f'{minutes(b)//60:02d}:{minutes(b)%60:02d}') for a,b in original]

    def capacity(self, grade, section):
        days, periods = self.layout(grade, section)
        return len(days) * len(periods)

    @staticmethod
    def _clock_minutes(value):
        if not re.fullmatch(r'\d{2}:\d{2}', str(value)):
            raise TimetableError('Enter period timings in 24-hour HH:MM format, e.g. 13:00.')
        h, m = map(int, value.split(':'))
        if h > 23 or m > 59:
            raise TimetableError('Invalid clock time: ' + value)
        return 60*h + m

    def validate_layout(self, day_count, period_times):
        try:
            day_count = int(day_count)
        except (TypeError, ValueError):
            raise TimetableError('Rows (school days) must be a whole number.')
        if not 1 <= day_count <= len(DAYS):
            raise TimetableError('School days must be between 1 and 6 (Monday-Saturday).')
        if not 1 <= len(period_times) <= 12:
            raise TimetableError('Periods per day must be between 1 and 12.')
        cleaned = []
        previous_end = -1
        for i, (start, end) in enumerate(period_times, 1):
            start = str(start).strip()
            end = str(end).strip()
            a = self._clock_minutes(start)
            b = self._clock_minutes(end)
            if not 6*60 <= a < b <= 22*60:
                raise TimetableError(f'Period {i}: start must precede end (06:00-22:00).')
            if a < previous_end:
                raise TimetableError(f'Period {i} overlaps the previous period.')
            cleaned.append((start, end))
            previous_end = b
        return day_count, cleaned

    def periods_overlap(self, grade_a, section_a, period_a, grade_b, section_b, period_b):
        _, periods_a = self.layout(grade_a, section_a)
        _, periods_b = self.layout(grade_b, section_b)
        if not (0 <= period_a < len(periods_a) and 0 <= period_b < len(periods_b)):
            # Stale saved schedules should be regenerated rather than crashing.
            return False
        a, b = periods_a[period_a]
        c, d = periods_b[period_b]
        return self._clock_minutes(a) < self._clock_minutes(d) and self._clock_minutes(c) < self._clock_minutes(b)

    def apply_layout_and_regenerate(self, grade, section, day_count, period_times, current_context):
        """Atomically save layout and rebuild this section's saved timetables.

        On any teacher clash or impossible quota, the old layout and schedules remain.
        """
        day_count, period_times = self.validate_layout(day_count, period_times)
        if grade not in GRADES or section not in self.sections(grade):
            raise TimetableError('Select a valid class and section.')
        capacity = day_count * len(period_times)
        if self.has_weekly_plan(grade, section):
            requested = sum(self.weekly_plan(grade, section).values())
            if requested > capacity:
                raise TimetableError(f'This class needs {requested} weekly periods but the new layout has {capacity}. '
                                     'Increase rows/columns or reduce subjects in Unit Dashboard first.')
        contexts = [tuple(row) + (grade, section) for row in self.db.execute(
            'SELECT DISTINCT year,month,week FROM schedules WHERE grade=? AND section=?', (grade, section))]
        selected = (*current_context[:3], grade, section)
        if selected not in contexts:
            contexts.append(selected)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('DELETE FROM schedules WHERE grade=? AND section=?', (grade, section))
            self.db.execute('INSERT OR REPLACE INTO timetable_layout VALUES(?,?,?,?)',
                            (grade, section, day_count, json.dumps(period_times)))
            scheduler = Scheduler(self)
            for i, context in enumerate(contexts):
                schedule = scheduler.generate(context, variation=i+137)
                self.validate(context, schedule)
                self.db.executemany("""INSERT INTO schedules
                    (year,month,week,grade,section,day,period,subject,teacher_override,bg,fg)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    [(*context, slot.day, slot.period, slot.subject, slot.teacher, slot.bg, slot.fg)
                     for slot in schedule.values()])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return len(contexts)

    def backup(self, dest):
        dest=Path(dest).resolve()
        if dest==self.path: raise TimetableError('Choose a different file for your backup.')
        dest.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(str(dest))) as other: self.db.backup(other)

    def has_users(self): return bool(self.db.execute('SELECT 1 FROM users LIMIT 1').fetchone())
    def add_user(self,username,password,role='viewer'):
        username=username.strip().lower()
        if not re.fullmatch('[a-z][a-z0-9_]{2,31}',username): raise TimetableError('Username: 3-32 letters, digits or underscores; start with a letter.')
        if not strong_password(password): raise TimetableError('Password needs 8+ characters, with letters and numbers.')
        if role not in ('admin','editor','viewer'): raise TimetableError('Invalid account role.')
        salt=os.urandom(16)
        digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000)
        with self.db: self.db.execute('INSERT INTO users(username,salt,password_hash,role,kdf_iterations) VALUES(?,?,?,?,?)',(username,salt,digest,role,310000))

    def login(self,username,password):
        row=self.db.execute('SELECT * FROM users WHERE username=?',(username.strip().lower(),)).fetchone()
        if not row:
            # Equal work for unknown usernames to reduce timing differences.
            hashlib.pbkdf2_hmac('sha256',password.encode(),b'0'*16,310000)
            return None
        digest=hashlib.pbkdf2_hmac('sha256',password.encode(),row['salt'],row['kdf_iterations'])
        return row['role'] if hmac.compare_digest(digest,row['password_hash']) else None

    def set_password(self,username,password):
        if not strong_password(password): raise TimetableError('Use 8+ characters, with letters and numbers.')
        salt=os.urandom(16)
        digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000)
        with self.db: self.db.execute('UPDATE users SET salt=?,password_hash=?,kdf_iterations=310000 WHERE username=?',(salt,digest,username))

    def rename_user(self, old_username, new_username):
        old_username = old_username.strip().lower()
        new_username = new_username.strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,31}', new_username):
            raise TimetableError('Username must be 3–32 letters/digits/underscores and start with a letter.')
        if not self.db.execute('SELECT 1 FROM users WHERE username=?',(old_username,)).fetchone():
            raise TimetableError('User not found.')
        if self.db.execute('SELECT 1 FROM users WHERE username=?',(new_username,)).fetchone():
            raise TimetableError('That username is already taken.')
        with self.db:
            self.db.execute('UPDATE users SET username=? WHERE username=?',(new_username,old_username))
            self.db.execute('UPDATE subject_access SET username=? WHERE username=?',(new_username,old_username))
        return new_username

    def update_credentials(self, old_username, new_username, new_password=None):
        """Update username and optional password in one transaction."""
        old_username=old_username.strip().lower()
        new_username=new_username.strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,31}',new_username):
            raise TimetableError('Username must be 3–32 letters/digits/underscores and start with a letter.')
        if not self.db.execute('SELECT 1 FROM users WHERE username=?',(old_username,)).fetchone():
            raise TimetableError('Current account was not found.')
        if new_username != old_username and self.db.execute('SELECT 1 FROM users WHERE username=?',(new_username,)).fetchone():
            raise TimetableError('That username is already taken.')
        if new_password and not strong_password(new_password):
            raise TimetableError('Password needs 8+ characters, with letters and numbers.')
        salt=os.urandom(16) if new_password else None
        digest=hashlib.pbkdf2_hmac('sha256',new_password.encode(),salt,310000) if new_password else None
        with self.db:
            if new_username!=old_username:
                self.db.execute('UPDATE users SET username=? WHERE username=?',(new_username,old_username))
                self.db.execute('UPDATE subject_access SET username=? WHERE username=?',(new_username,old_username))
            if new_password:
                self.db.execute('UPDATE users SET salt=?,password_hash=?,kdf_iterations=310000 WHERE username=?',
                                (salt,digest,new_username))
        return new_username

    def grant_subject(self,username,grade,section,subject):
        username=username.strip().lower()
        if not self.db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
            raise TimetableError('Create the teacher account first.')
        if grade not in GRADES or section not in self.sections(grade):
            raise TimetableError('Choose a valid class and section.')
        subject=subject.strip()
        if not subject or len(subject)>65:raise TimetableError('Choose a valid subject.')
        with self.db:self.db.execute('INSERT OR IGNORE INTO subject_access VALUES(?,?,?,?)',
                                     (username,grade,section,subject))

    def revoke_subject(self,username,grade,section,subject):
        with self.db:self.db.execute('DELETE FROM subject_access WHERE username=? AND grade=? AND section=? AND subject=?',
                                     (username.strip().lower(),grade,section,subject))

    def grants(self,username,grade=None,section=None):
        query='SELECT grade,section,subject FROM subject_access WHERE username=?'
        params=[username.strip().lower()]
        if grade is not None:query+=' AND grade=?';params.append(grade)
        if section is not None:query+=' AND section=?';params.append(section)
        return [tuple(row) for row in self.db.execute(query+' ORDER BY grade,section,subject',params)]

    def ensure_assigned_schedule(self, context, subject):
        """Create and save a missing schedule when a teacher is granted a subject.

        Never replace an existing timetable or invent a subject outside its
        configured plan. Both class and revision modes keep independent data.
        Returns (available, explanation). A grant alone is not a lesson.
        """
        year, month, week, grade, section = context
        normalized = str(subject).strip().casefold()
        if not normalized:
            return False, 'Choose a subject.'
        plan = self.weekly_plan(grade, section)
        if normalized not in {name.strip().casefold() for name in plan}:
            return False, 'This subject is not in the weekly subject plan for this class/section. Add it in Unit Dashboard first.'
        existing = self.load(context)
        if existing:
            found = any(slot.subject.strip().casefold() == normalized for slot in existing.values())
            return found, ('Subject already appears in this saved timetable.' if found else
                           'This saved timetable has no period for that subject. Review the existing schedule as administrator.')
        # Do not replace other weeks or any previously saved schedule.
        try:
            slots = Scheduler(self).generate(context)
            if not any(slot.subject.strip().casefold() == normalized for slot in slots.values()):
                return False, 'The generated timetable did not contain the selected subject.'
            self.save(context, slots)
            return True, 'Timetable generated and automatically saved to SQLite.'
        except (TimetableError, ValueError) as error:
            return False, str(error)

    def authorized_contexts(self, username, grade=None, section=None):
        """Only saved class/week contexts containing this user's granted subjects.

        A grant is not a scheduled lesson: a viewer must never see another
        subject simply because a class/section matches. Works for either the
        class or independent revision database when called on that Store.
        """
        if grade is not None and grade not in GRADES:
            return []
        # Pass the grants from the main accounts database via the UI when
        # looking in the separate revision store; see contexts_for_subjects.
        return self.contexts_for_subjects(self.grants(username,grade,section))

    def contexts_for_subjects(self, grants):
        """Find saved year/month/week/class/section containing authorized lessons."""
        result=set()
        for grade,section,subject in grants:
            rows=self.db.execute("""SELECT DISTINCT year,month,week FROM schedules
                WHERE grade=? AND section=? AND lower(trim(subject))=?""",
                (grade,section,str(subject).strip().lower())).fetchall()
            result.update((row['year'],row['month'],row['week'],grade,section) for row in rows)
        month_numbers={name:index for index,name in enumerate(calendar.month_name) if name}
        def order(ctx):
            try: week=int(ctx[2].split()[-1])
            except (ValueError,IndexError):week=0
            return (ctx[0],month_numbers.get(ctx[1],0),week,ctx[3],ctx[4])
        return sorted(result,key=order,reverse=True)

    def scoped_load(self,context,allowed):
        # Only return rows for authorized subjects.
        if not allowed:return {}
        # Subject names in historical schedules may differ only in capitalization
        # or surrounding spaces from an administrator's grant.  Keep all other
        # subjects hidden; do not grant access by a partial/substring match.
        normalized={str(name).strip().casefold() for name in allowed}
        return {key:slot for key,slot in self.load(context).items()
                if slot.subject.strip().casefold() in normalized}

    def save_subject_edits(self,context,changes,allowed):
        # Update only authorized cells; never overwrite other lessons.
        allowed={str(name).strip().casefold() for name in allowed}
        if not allowed:raise TimetableError('No subjects assigned to this account.')
        original=self.load(context)
        merged=dict(original)
        for key,slot in changes.items():
            if (key not in original or original[key].subject.strip().casefold() not in allowed
                    or slot.subject.strip().casefold() not in allowed):
                raise TimetableError('You may edit only your assigned subjects.')
            if key!=(slot.day,slot.period):raise TimetableError('Invalid timetable position.')
            merged[key]=slot
        self.validate(context,merged)
        year,month,week,grade,section=context
        with self.db:
            for (day,period),slot in changes.items():
                cursor=self.db.execute('''UPDATE schedules SET subject=?,teacher_override=?,bg=?,fg=?
                    WHERE year=? AND month=? AND week=? AND grade=? AND section=? AND day=? AND period=? AND subject=?''',
                    (slot.subject,slot.teacher,slot.bg,slot.fg,year,month,week,grade,section,day,period,
                     original[(day,period)].subject))
                if cursor.rowcount!=1:raise TimetableError('Timetable changed; reload and try again.')

    def teacher_profiles(self):
        return self.db.execute('SELECT id,name,description FROM teacher_profiles ORDER BY name COLLATE NOCASE').fetchall()

    def add_teacher_profile(self,name,description=''):
        name=str(name).strip();description=str(description).strip()
        if not name or len(name)>100 or len(description)>500:
            raise TimetableError('Teacher name must be 1–100 characters and description at most 500.')
        with self.db:
            return self.db.execute('INSERT INTO teacher_profiles(name,description) VALUES(?,?)',
                                   (name,description)).lastrowid

    def update_teacher_profile(self,identifier,name,description=''):
        name=str(name).strip();description=str(description).strip()
        if not name or len(name)>100 or len(description)>500:
            raise TimetableError('Teacher name must be 1–100 characters and description at most 500.')
        with self.db:
            cursor=self.db.execute('UPDATE teacher_profiles SET name=?,description=? WHERE id=?',
                                   (name,description,int(identifier)))
            if cursor.rowcount!=1:raise TimetableError('Teacher not found.')

    def delete_teacher_profile(self,identifier):
        with self.db:
            self.db.execute('DELETE FROM teacher_profiles WHERE id=?',(int(identifier),))

    def get_setting(self,key,default=None):
        row=self.db.execute('SELECT value FROM settings WHERE key=?',(str(key),)).fetchone()
        return row['value'] if row else default

    def set_setting(self,key,value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                            (str(key),str(value)))

    def log(self,username,event):
        with self.db: self.db.execute('INSERT INTO audit(created_at,username,event) VALUES(?,?,?)',(datetime.now().isoformat(timespec='seconds'),username,event))

    def clear_history(self, username):
        """Delete previous audit entries and record the administrator's action."""
        with self.db:
            self.db.execute('DELETE FROM audit')
            self.db.execute('INSERT INTO audit(created_at,username,event) VALUES(?,?,?)',
                            (datetime.now().isoformat(timespec='seconds'),
                             str(username), 'Administrator cleared login history'))

    def sections(self,grade):
        found=[r[0] for r in self.db.execute('SELECT section FROM sections WHERE grade=? ORDER BY section',(grade,))]
        if grade in ('11','12'):
            return [s for s in found if s not in ('B1','B2')]
        return found
    def add_section(self,grade,section):
        section=section.strip().upper()
        if not re.fullmatch('[A-Z][A-Z0-9]{0,5}',section): raise TimetableError('Section must be 1-6 letters/numbers, starting with a letter.')
        with self.db: self.db.execute('INSERT INTO sections VALUES(?,?)',(grade,section))
    def rename_section(self, grade, old_section, new_section):
        """Rename a section atomically, preserving all its linked timetable records."""
        new_section = str(new_section).strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9 -]{0,23}', new_section):
            raise TimetableError('Use 1-24 letters, numbers, spaces or hyphens; start with a letter.')
        if old_section not in self.sections(grade):
            raise TimetableError('The original section does not exist.')
        if new_section == old_section:
            return new_section
        if any(sec.casefold() == new_section.casefold() for sec in self.sections(grade)):
            raise TimetableError('A section with that name already exists in this class.')
        tables = ('sections', 'assignments', 'rules', 'schedules',
                  'weekly_subject_periods', 'weekly_plan_enabled',
                  'student_counts', 'timetable_layout', 'subject_access')
        with self.db:
            for table in tables:
                self.db.execute(
                    f'UPDATE {table} SET section=? WHERE grade=? AND section=?',
                    (new_section, grade, old_section))
        return new_section

    def delete_section(self,grade,section):
        if len(self.sections(grade)) <= 1: raise TimetableError('At least one section must remain.')
        with self.db:
            self.db.execute('DELETE FROM sections WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM assignments WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM rules WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM weekly_subject_periods WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM weekly_plan_enabled WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM schedules WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM student_counts WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM timetable_layout WHERE grade=? AND section=?',(grade,section))
            self.db.execute('DELETE FROM subject_access WHERE grade=? AND section=?',(grade,section))

    def has_weekly_plan(self,grade,section):
        return bool(self.db.execute('SELECT 1 FROM weekly_plan_enabled WHERE grade=? AND section=?',
                                    (grade,section)).fetchone())

    def weekly_plan(self,grade,section):
        """Return {subject: exact periods/week}. Before customization, show a suggested plan."""
        if self.has_weekly_plan(grade,section):
            return {r['subject']:r['periods'] for r in self.db.execute(
                'SELECT subject,periods FROM weekly_subject_periods WHERE grade=? AND section=? ORDER BY subject',
                (grade,section))}
        mains,extras=self.subjects(grade,section)
        counts=Counter(extras)
        if group(grade)=='middle': counts['SKKA']=max(2,counts.get('SKKA',0))
        capacity=self.capacity(grade,section)
        # If default extra activities exceed available periods, they must be edited.
        free=max(0,capacity-sum(counts.values()))
        unique_mains=list(dict.fromkeys(mains))
        for i in range(free):
            counts[unique_mains[i % len(unique_mains)]]+=1
        return dict(sorted(counts.items()))

    def save_weekly_plan(self,grade,section,plan):
        """Persist manual subject counts. Empty slots are explicitly '---'."""
        if grade not in GRADES or section not in self.sections(grade):
            raise TimetableError('Select a valid class and section.')
        clean={}
        for subject,quantity in plan.items():
            subject=str(subject).strip()
            if not subject or len(subject)>55 or subject=='---':
                raise TimetableError('Enter a valid subject name (1-55 characters, not ---).')
            if any(existing.casefold()==subject.casefold() for existing in clean):
                raise TimetableError('Duplicate subject names are not allowed.')
            try: number=int(quantity)
            except (ValueError,TypeError): raise TimetableError(f'{subject}: periods must be a whole number.')
            if str(quantity).strip()!=str(number) or number<1:
                raise TimetableError(f'{subject}: enter a positive whole number.')
            clean[subject]=number
        if not clean: raise TimetableError('Add at least one subject.')
        total=sum(clean.values());capacity=self.capacity(grade,section)
        if total>capacity:
            raise TimetableError(f'{grade}-{section}: {total} periods requested, but only {capacity} are available. Reduce by {total-capacity}.')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO weekly_plan_enabled VALUES(?,?)',(grade,section))
            self.db.execute('DELETE FROM weekly_subject_periods WHERE grade=? AND section=?',(grade,section))
            self.db.executemany('INSERT INTO weekly_subject_periods VALUES(?,?,?,?)',
                                [(grade,section,subject,count) for subject,count in clean.items()])

    def apply_plan_and_regenerate(self, grade, sections, plan, current_context, variation=0):
        '''Atomically update a plan and every saved timetable for target sections.

        Include the currently displayed academic period even if no schedule exists.
        If any schedule cannot be made clash-free, restore all old plans and
        timetables. Never modify schedules for unrelated classes or sections.
        '''
        sections = list(dict.fromkeys(sections))
        if not sections:
            raise TimetableError('Choose at least one section.')
        if any(sec not in self.sections(grade) for sec in sections):
            raise TimetableError('Invalid section selected.')
        # Validate all subject counts before changing any records.
        clean = {}
        for subject, quantity in plan.items():
            subject = str(subject).strip()
            if not subject or subject == '---' or len(subject) > 55:
                raise TimetableError('Subject name must be 1-55 characters (not ---).')
            if any(x.casefold() == subject.casefold() for x in clean):
                raise TimetableError('Duplicate subject name.')
            try:
                count = int(quantity)
            except (ValueError, TypeError):
                raise TimetableError(f'{subject}: enter a positive whole number.')
            if count < 1 or str(quantity).strip() != str(count):
                raise TimetableError(f'{subject}: enter a positive whole number.')
            clean[subject] = count
        if not clean:
            raise TimetableError('At least one subject is required.')
        for target_section in sections:
            if sum(clean.values()) > self.capacity(grade,target_section):
                raise TimetableError(f'{grade}-{target_section}: only {self.capacity(grade,target_section)} weekly periods are available.')

        contexts = []
        for section in sections:
            rows = self.db.execute('''SELECT DISTINCT year,month,week FROM schedules
                WHERE grade=? AND section=? ORDER BY year,month,week''',
                (grade, section)).fetchall()
            contexts.extend((r['year'],r['month'],r['week'],grade,section) for r in rows)
            selected = (*current_context[:3],grade,section)
            if selected not in contexts:
                contexts.append(selected)
        contexts.sort()

        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Remove the old target schedules inside the transaction so they
            # cannot incorrectly block teacher availability for the new plan.
            for section in sections:
                self.db.execute('DELETE FROM schedules WHERE grade=? AND section=?',
                                (grade, section))
                self.db.execute('INSERT OR IGNORE INTO weekly_plan_enabled VALUES (?,?)',
                                (grade, section))
                self.db.execute('DELETE FROM weekly_subject_periods WHERE grade=? AND section=?',
                                (grade, section))
                self.db.executemany('INSERT INTO weekly_subject_periods VALUES (?,?,?,?)',
                    [(grade,section,subject,count) for subject,count in clean.items()])
            scheduler = Scheduler(self)
            for index, ctx in enumerate(contexts):
                slots = scheduler.generate(ctx, variation=variation + index + 1)
                self.validate(ctx, slots)
                self.db.executemany('''INSERT INTO schedules
                    (year,month,week,grade,section,day,period,subject,teacher_override,bg,fg)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    [(*ctx,slot.day,slot.period,slot.subject,slot.teacher,slot.bg,slot.fg)
                     for slot in slots.values()])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return len(contexts)

    def subjects(self,grade,section):
        if self.has_weekly_plan(grade,section): return list(self.weekly_plan(grade,section)), []
        row=self.db.execute('SELECT main,extras FROM rules WHERE grade=? AND section=?',(grade,section)).fetchone()
        if row: return [a.strip() for a in row['main'].split(',') if a.strip()], [a.strip() for a in row['extras'].split(',') if a.strip()]
        return default_subjects(grade,section)
    def set_subjects(self,grade,section,mains,extras):
        if not mains or any(not s.strip() for s in mains): raise TimetableError('Enter at least one main subject.')
        if any(len(s)>55 for s in mains+extras): raise TimetableError('Subject name is too long.')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO rules VALUES(?,?,?,?)',(grade,section,','.join(mains),','.join(extras)))

    def assignments(self,grade=None,section=None):
        q='SELECT grade,section,subject,teacher FROM assignments WHERE 1=1';p=[]
        if grade: q+=' AND grade=?';p.append(grade)
        if section: q+=' AND section=?';p.append(section)
        return [dict(r) for r in self.db.execute(q+' ORDER BY grade,section,subject',p)]
    def teacher_for(self,grade,section,subject):
        row=self.db.execute('SELECT teacher FROM assignments WHERE grade=? AND section=? AND subject=?',(grade,section,subject)).fetchone()
        return row['teacher'] if row else ''
    def assign(self,grade,section,subject,teacher):
        subject=subject.strip();teacher=teacher.strip()
        if not subject or not teacher: raise TimetableError('Subject and teacher must not be blank.')
        if len(teacher)>100 or len(subject)>55: raise TimetableError('Teacher/subject is too long.')
        with self.db: self.db.execute('INSERT OR REPLACE INTO assignments VALUES(?,?,?,?)',(grade,section,subject,teacher))
    def unassign(self,grade,section,subject):
        with self.db: self.db.execute('DELETE FROM assignments WHERE grade=? AND section=? AND subject=?',(grade,section,subject))

    def students(self,grade,section):
        r=self.db.execute('SELECT students FROM student_counts WHERE grade=? AND section=?',(grade,section)).fetchone()
        return r[0] if r else 40
    def set_students(self,grade,section,number):
        try: n=int(number)
        except (ValueError,TypeError): raise TimetableError('Student count must be a number.')
        if not (0<=n<=500): raise TimetableError('Student count must be between 0 and 500.')
        with self.db: self.db.execute('INSERT OR REPLACE INTO student_counts VALUES(?,?,?)',(grade,section,n))

    def load(self,context):
        year,month,week,grade,section=context
        rows=self.db.execute('''SELECT day,period,subject,teacher_override,bg,fg FROM schedules
          WHERE year=? AND month=? AND week=? AND grade=? AND section=?''',context).fetchall()
        return {(r['day'],r['period']):Slot(r['day'],r['period'],r['subject'],r['teacher_override'],r['bg'],r['fg']) for r in rows}

    def other_schedules(self,context):
        year,month,week,grade,section=context
        rows=self.db.execute('''SELECT grade,section,day,period,subject,teacher_override FROM schedules
          WHERE year=? AND month=? AND week=? AND NOT (grade=? AND section=?)''',context).fetchall()
        return [dict(r) for r in rows]

    def conflicts(self,context,slots):
        year,month,week,grade,section=context
        others=self.other_schedules(context)
        errors=[]
        for slot in slots.values():
            if slot.subject in ('---',''): continue
            teacher=slot.teacher.strip() or self.teacher_for(grade,section,slot.subject)
            if not teacher: continue
            for other in others:
                ot=other['teacher_override'].strip() or self.teacher_for(other['grade'],other['section'],other['subject'])
                if (teacher_set(ot) & teacher_set(teacher) and slot.day==other['day'] and
                    self.periods_overlap(grade,section,slot.period,other['grade'],other['section'],other['period'])):
                    errors.append(f'{teacher}: {slot.day} period {slot.period+1} in {grade}-{section} '
                                  f'overlaps {other["grade"]}-{other["section"]} period {other["period"]+1}')
        return sorted(set(errors))

    def validate(self,context,slots):
        _,_,_,grade,_=context
        allowed_days, period_times = self.layout(grade,context[4]); n=len(period_times)
        for (day,p),slot in slots.items():
            if day not in allowed_days or not isinstance(p,int) or not 0<=p<n:
                raise TimetableError('Invalid timetable day or period.')
            if not slot.subject.strip(): raise TimetableError('Empty subject: use --- if no lesson is planned.')
        if self.has_weekly_plan(grade,context[4]):
            capacity=self.capacity(grade,context[4])
            if len(slots)!=capacity:
                raise TimetableError(f'This class needs {capacity} timetable cells; found {len(slots)}. Generate a new timetable.')
            expected=self.weekly_plan(grade,context[4]); actual=Counter(s.subject for s in slots.values())
            mismatches=[f'{subject}: expected {target}, found {actual[subject]}'
                        for subject,target in expected.items() if actual[subject]!=target]
            unknown=[subject for subject in actual if subject not in expected and subject!='---']
            unallocated=capacity-sum(expected.values())
            if actual['---']!=unallocated:
                mismatches.append(f'Unallocated: expected {unallocated}, found {actual["---"]}')
            if unknown:mismatches.append('Not in the weekly plan: '+', '.join(unknown))
            if mismatches:
                raise TimetableError('Weekly subject periods do not match the Unit Dashboard:\n'+'\n'.join(mismatches[:12])+
                                     '\nUse Unit Dashboard to update periods automatically, or swap two periods to preserve counts.')
        problems=self.conflicts(context,slots)
        if problems: raise TimetableError('Teacher conflicts:\n'+'\n'.join(problems[:12]))

    def save(self,context,slots):
        # BEGIN IMMEDIATE prevents a competing writer changing assignments or
        # timetable slots between clash validation and committing the save.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.validate(context,slots)
            self.db.execute('DELETE FROM schedules WHERE year=? AND month=? AND week=? AND grade=? AND section=?',context)
            for slot in slots.values():
                self.db.execute('''INSERT INTO schedules(year,month,week,grade,section,day,period,subject,teacher_override,bg,fg)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(*context,slot.day,slot.period,slot.subject,slot.teacher,slot.bg,slot.fg))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

class Scheduler:
    def __init__(self,store): self.store=store
    def generate(self,context,variation=0):
        if self.store.has_weekly_plan(context[3],context[4]):
            schedule = self._generate_exact(context, variation=variation)
            return self._diversify(context, schedule, variation)
        year,month,week,grade,section=context
        days, period_times = self.store.layout(grade,section)
        count=len(period_times)
        mains,extras=self.store.subjects(grade,section)
        if not mains: raise TimetableError('No main subjects configured.')
        fixed={}
        if group(grade)=='middle' and 'Friday' in days and count >= 2:
            fixed[('Friday',count-2)]='SKKA'
            fixed[('Friday',count-1)]='SKKA'
        if count >= 2 and group(grade) in ('primary','middle') and 'PET' in extras and 'Sakthi Neri' in extras:
            candidate_days=[d for d in days if d!='Friday' or group(grade)!='middle']
            ix=int(hashlib.sha256(('/'.join(context)).encode()).hexdigest(),16)%len(candidate_days)
            fixed[(candidate_days[ix],count-2)]='PET'
            fixed[(candidate_days[ix],count-1)]='Sakthi Neri'
        # Additional activities are scheduled once per occurrence; repeated
        # 'PET' etc. entries create proportionate subject frequencies.
        extra_pool=[x for x in extras if not (x=='SKKA' and group(grade)=='middle')]
        for required in fixed.values():
            if required in extra_pool: extra_pool.remove(required)
        free_places=len(days)*count-len(fixed)
        if len(extra_pool)>free_places:
            raise TimetableError('Too many extra subjects for available periods.')
        # A reproducible seed means the same parameters give consistent output.
        seed=int.from_bytes(hashlib.sha256('|'.join(context).encode()).digest()[:8],'big')
        other=self.store.other_schedules(context)
        occupied=[]
        for r in other:
            teacher=r['teacher_override'].strip() or self.store.teacher_for(r['grade'],r['section'],r['subject'])
            for name in teacher_set(teacher): occupied.append((name,r['day'],r['grade'],r['section'],r['period']))
        def available(subject,day,period):
            teachers=teacher_set(self.store.teacher_for(grade,section,subject))
            return not teachers or all(not (oth in teachers and day==d and self.store.periods_overlap(grade,section,period,g,other_section,p)) for oth,d,g,other_section,p in occupied)
        for attempt in range(350):
            rng=random.Random(seed+attempt*2039)
            schedule={k:Slot(k[0],k[1],v) for k,v in fixed.items()}
            if any(not available(s.subject,s.day,s.period) for s in schedule.values()):
                raise TimetableError('A fixed school activity creates a teacher clash. Change its teacher assignment.')
            counts=Counter(s.subject for s in schedule.values())
            remaining_extras=list(extra_pool)
            cells=[(d,p) for d in days for p in range(count) if (d,p) not in schedule]
            # Fill most-constrained time slots first to improve the chance of finding a feasible schedule.
            rng.shuffle(cells)
            cells.sort(key=lambda cell:sum(available(s,cell[0],cell[1]) for s in set(mains+extras)))
            failed=False
            for day,p in cells:
                choices=[]
                for subject in sorted(set(mains+remaining_extras)):
                    if not available(subject,day,p): continue
                    earlier=sum(1 for (d,_),s in schedule.items() if d==day and s.subject==subject)
                    repeats=counts[subject]
                    # Prefer extras with outstanding frequency, then balance mains.
                    demand=remaining_extras.count(subject)
                    is_extra=subject in remaining_extras
                    weight=(12+demand*4 if is_extra else 5) - earlier*4 - repeats*0.35 + rng.random()*2
                    choices.append((weight,subject))
                if not choices: failed=True;break
                choices.sort(reverse=True)
                subject=choices[0][1]
                schedule[(day,p)]=Slot(day,p,subject)
                counts[subject]+=1
                if subject in remaining_extras: remaining_extras.remove(subject)
            if failed or remaining_extras: continue
            if not self.store.conflicts(context,schedule):
                return self._diversify(context,schedule,variation)
        raise TimetableError('No clash-free timetable found with these teacher assignments. '
                             'Try changing assignments or rules; no invalid timetable was saved.')

    def _diversify(self,context,schedule,variation=0):
        '''Reorder subjects *within each day* to avoid identical daily columns.

        Weekly quotas and day-level frequencies remain exactly unchanged.
        Fixed Friday SKKA lessons are preserved. Existing teacher availability
        across saved classes is checked before accepting a swap.
        '''
        _,_,_,grade,section = context
        days, period_times = self.store.layout(grade,section)
        periods = len(period_times)
        seed = int.from_bytes(hashlib.sha256('|'.join(context).encode()).digest()[:8],'big')
        rng = random.Random(seed + int(variation))
        other = self.store.other_schedules(context)
        occupied = []
        for row in other:
            teacher = (row['teacher_override'].strip() or
                       self.store.teacher_for(row['grade'],row['section'],row['subject']))
            if teacher:
                occupied.extend((name,row['day'],row['grade'],row['section'],row['period']) for name in teacher_set(teacher))

        def available(slot,day,period):
            teacher = (slot.teacher.strip() or
                       self.store.teacher_for(grade,section,slot.subject))
            teachers=teacher_set(teacher)
            return not teachers or all(
                not (t in teachers and day == d and self.store.periods_overlap(grade,section,period,g,other_section,p))
                for t,d,g,other_section,p in occupied)

        result = {}
        previous = []
        for day in days:
            slots = [schedule[(day,p)] for p in range(periods)]
            locked = set()
            if grade in ('6','7','8'):
                for p in (periods-2,periods-1):
                    if slots[p].subject == 'SKKA':
                        locked.add(p)
            movable = [p for p in range(periods) if p not in locked]
            best = slots[:]
            best_score = float('inf')
            for attempt in range(260):
                candidate = slots[:]
                shuffled = [slots[p] for p in movable]
                if attempt:
                    rng.shuffle(shuffled)
                for p,slot in zip(movable,shuffled):
                    candidate[p] = slot
                if any(not available(slot,day,p) for p,slot in enumerate(candidate)):
                    continue
                score = 0
                for p,slot in enumerate(candidate):
                    if slot.subject == '---':
                        continue
                    # Repeating the same subject in the same period on other
                    # days is penalized heavily.
                    score += 35 * sum(
                        earlier[p] == slot.subject for earlier in previous)
                    if p and candidate[p-1].subject == slot.subject:
                        score += 12
                row = tuple(slot.subject for slot in candidate)
                score += 45 * sum(tuple(old) == row for old in previous)
                score += rng.random() * 0.2
                if score < best_score:
                    best = candidate[:]
                    best_score = score
                    if score < 0.2:
                        break
            for p,slot in enumerate(best):
                result[(day,p)] = Slot(day,p,slot.subject,slot.teacher,slot.bg,slot.fg)
            previous.append([slot.subject for slot in best])
        self.store.validate(context,result)
        return result

    def _generate_exact(self,context,variation=0):
        """Min-cost flow: exact subject frequencies with teacher availability.

        A low cost for the first daily occurrence of a subject encourages
        distribution across the week. Flow guarantees quotas if feasible.
        """
        _,_,_,grade,section=context
        days, period_times = self.store.layout(grade,section)
        periods=len(period_times)
        plan=self.store.weekly_plan(grade,section)
        total=sum(plan.values())
        capacity=len(days)*periods
        if total>capacity:raise TimetableError('Weekly period total exceeds available slots.')
        if total<capacity:plan['---']=capacity-total
        fixed={}
        if grade in ('6','7','8') and 'Friday' in days and periods >= 2 and plan.get('SKKA',0)>=2:
            for p in (periods-2,periods-1):
                fixed[('Friday',p)]='SKKA'
            plan['SKKA']-=2
        other=self.store.other_schedules(context)
        occupied=[]
        for row in other:
            teacher=row['teacher_override'].strip() or self.store.teacher_for(row['grade'],row['section'],row['subject'])
            if teacher:occupied.extend((name,row['day'],row['grade'],row['section'],row['period']) for name in teacher_set(teacher))
        def available(subject,day,p):
            if subject=='---':return True
            teachers=teacher_set(self.store.teacher_for(grade,section,subject))
            return not teachers or all(not (t in teachers and day==d and self.store.periods_overlap(grade,section,p,g,other_section,q))
                                      for t,d,g,other_section,q in occupied)
        for (day,p),subject in fixed.items():
            if not available(subject,day,p):
                raise TimetableError(f'Fixed SKKA period on {day} period {p+1} clashes with a saved teacher assignment.')
        free=[(d,p) for d in days for p in range(periods) if (d,p) not in fixed]
        # Min-cost max-flow graph. Every subject must fill its exact quota.
        graph=[]
        def node():graph.append([]);return len(graph)-1
        def edge(a,b,cap,cost):
            graph[a].append([b,len(graph[b]),cap,cost])
            graph[b].append([a,len(graph[a])-1,0,-cost])
            return graph[a][-1]
        source=node()
        subject_nodes={s:node() for s,q in plan.items() if q>0}
        slot_nodes={slot:node() for slot in free}
        sink=node()
        for slot,n in slot_nodes.items():edge(n,sink,1,0)
        rng=random.Random(int.from_bytes(hashlib.sha256('|'.join(context).encode()).digest()[:8],'big') + variation)
        assignments=[]
        subjects=list(subject_nodes)
        rng.shuffle(subjects)
        for subject in subjects:
            qty=plan[subject]
            sn=subject_nodes[subject]
            edge(source,sn,qty,0)
            for day in days:
                candidates=[slot for slot in free if slot[0]==day and available(subject,*slot)]
                if not candidates:continue
                dn=node()
                # Extra repetitions of the same subject on one day cost more.
                for nth in range(min(qty,len(candidates))):
                    edge(sn,dn,1,(nth*nth*5 if subject!='---' else 0))
                rng.shuffle(candidates)
                for slot in candidates:
                    e=edge(dn,slot_nodes[slot],1,0)
                    assignments.append((subject,slot,e))
        demand=len(free)
        potential=[0]*len(graph)
        flow=0
        while flow<demand:
            dist=[float('inf')]*len(graph)
            prev=[None]*len(graph)
            dist[source]=0
            pq=[(0,source)]
            while pq:
                cost,u=heapq.heappop(pq)
                if cost!=dist[u]:continue
                for idx,e in enumerate(graph[u]):
                    v,_,cap,w=e
                    if cap<=0:continue
                    nd=cost+w+potential[u]-potential[v]
                    if nd<dist[v]:
                        dist[v]=nd;prev[v]=(u,idx)
                        heapq.heappush(pq,(nd,v))
            if prev[sink] is None:break
            for i,d in enumerate(dist):
                if d!=float('inf'):potential[i]+=d
            v=sink
            while v!=source:
                u,idx=prev[v]
                e=graph[u][idx]
                e[2]-=1
                graph[v][e[1]][2]+=1
                v=u
            flow+=1
        if flow!=demand:
            raise TimetableError('Cannot fit all weekly subject periods without teacher clashes. '
                                 'Review teacher assignments or reduce/rearrange requirements.')
        schedule={(d,p):Slot(d,p,s) for (d,p),s in fixed.items()}
        for subject,slot,e in assignments:
            if e[2]==0:
                if slot in schedule:raise TimetableError('Internal scheduling conflict.')
                schedule[slot]=Slot(slot[0],slot[1],subject)
        if len(schedule)!=capacity:
            raise TimetableError('Timetable generation incomplete; nothing was saved.')
        self.store.validate(context,schedule)
        return schedule
