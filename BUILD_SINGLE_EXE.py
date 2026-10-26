"""Build ONE Windows EXE containing the APMHSS program, images and a saved database snapshot."""
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

BASE = Path(__file__).resolve().parent
OUT = BASE / 'SINGLE_EXE_READY'
NAME = 'APMHSS_Time_Table'
REQUIRED = [
    'CLASS_TIME_TABLE_GENERATOR.py', 'timetable_core.py', 'timetable_export.py',
    'school_logo.png', 'school_photo.jpg', 'Adhiparasakthi_Time_Table_Icon.ico',
    'requirements.txt',
]


def valid_database(db_path):
    if not db_path.is_file():
        return False
    try:
        with closing(sqlite3.connect(str(db_path))) as db:
            def columns(table):
                return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            return ({'username','salt'} <= columns('users')
                    and bool({'password_hash','digest'} & columns('users'))
                    and {'year','month','week','grade','section','day','period','subject'} <= columns('schedules')
                    and db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None)
    except sqlite3.Error:
        return False


def choose_database():
    # The explicit import is safest: it avoids silently embedding the wrong PC's data.
    candidates = [
        BASE / 'data' / 'school_timetable.sqlite3',
        BASE / 'READY_TO_RUN' / 'data' / 'school_timetable.sqlite3',
    ]
    return next((p for p in candidates if valid_database(p)), None)


def main():
    if os.name != 'nt':
        raise RuntimeError('Build the Windows EXE on your Windows laptop.')
    missing = [name for name in REQUIRED if not (BASE / name).is_file()]
    if missing:
        raise FileNotFoundError('Missing source files: ' + ', '.join(missing))

    source = choose_database()
    if source is None:
        raise RuntimeError(
            'No saved APMHSS database selected. Run IMPORT_EXISTING_DATA.bat first.\n'
            'Select the school_timetable.sqlite3 file inside your OLD app data folder.\n'
            'This prevents building an EXE that asks you to create admin again.')

    print('Existing database:', source, flush=True)
    print('STEP 1/2 - Installing PyInstaller and requirements...', flush=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(BASE / 'requirements.txt'),
                    'pyinstaller'], cwd=BASE, check=True)

    print('STEP 2/2 - Building a SINGLE EXE with the saved data snapshot...', flush=True)
    OUT.mkdir(exist_ok=True)
    # Important Windows fix: do not put the DB snapshot in TemporaryDirectory.
    # PyInstaller/antivirus may briefly hold the file open after the EXE is built,
    # which caused WinError 32 during automatic TemporaryDirectory cleanup.
    # Keep the staging snapshot outside the distributable SINGLE_EXE_READY folder.
    staging = BASE / '_BUILD_SNAPSHOT_DO_NOT_TRANSFER'
    staging.mkdir(exist_ok=True)
    snapshot = staging / 'school_timetable.sqlite3'

    # A sqlite3 connection context manager commits but DOES NOT close the DB.
    # closing() releases all handles before PyInstaller reads the snapshot.
    with closing(sqlite3.connect(str(source), timeout=30)) as db:
        with closing(sqlite3.connect(str(snapshot), timeout=30)) as destination:
            db.backup(destination)
    if not valid_database(snapshot):
        raise RuntimeError('Database snapshot verification failed.')

    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onefile', '--windowed', '--name', NAME,
        '--icon', str(BASE / 'Adhiparasakthi_Time_Table_Icon.ico'),
        '--paths', str(BASE),
        '--distpath', str(OUT),
        '--workpath', str(BASE / 'build'),
        '--specpath', str(BASE),
    ]
    for filename in ('school_logo.png', 'school_photo.jpg', 'Adhiparasakthi_Time_Table_Icon.ico'):
        command.extend(['--add-data', str(BASE / filename) + os.pathsep + '.'])
    command.extend(['--add-data', str(snapshot) + os.pathsep + 'data'])
    command.append(str(BASE / 'CLASS_TIME_TABLE_GENERATOR.py'))
    subprocess.run(command, cwd=BASE, check=True)

    exe = OUT / (NAME + '.exe')
    if not exe.is_file():
        raise FileNotFoundError('PyInstaller finished without creating the EXE.')
    print('\nSUCCESS - SINGLE FILE:', exe, flush=True)
    print('Note: The staging snapshot remains in the builder folder; do not share it.', flush=True)
    print('Copy only this EXE to another PC. Your saved database snapshot is inside it.', flush=True)
    print('On each PC, changes are saved separately under LOCALAPPDATA\\APMHSS_Time_Table.', flush=True)
    print('To carry NEW changes, import the latest database and REBUILD the EXE.', flush=True)
    try:
        os.startfile(str(OUT))
    except OSError:
        pass


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('\nBUILD FAILED:', exc, flush=True)
        sys.exit(1)
