APMHSS SINGLE EXE BUILDER - WINDOWS
==================================

GOAL: Transfer ONE APMHSS_Time_Table.exe, with the saved admin account,
subject plans, school images, period timings and timetables already embedded.

IMPORTANT TECHNICAL LIMITATION
------------------------------
The EXE includes a snapshot of your database taken at BUILD time.
SQLite must be writable, so the running app copies that snapshot into:
  %LOCALAPPDATA%\APMHSS_Time_Table\school_timetable.sqlite3
The app writes changes there. It cannot reliably write changes back into
its own running EXE. Therefore, when moving later changes to another PC,
IMPORT the latest database and REBUILD the EXE. Copying the existing EXE
again will transfer the OLD snapshot, not any newer edits.

STEPS
-----
1. Extract this complete ZIP (do not run directly from inside ZIP).
2. Close your old APMHSS app on the original PC.
3. Double-click IMPORT_EXISTING_DATA.bat.
4. Choose the OLD app's data\school_timetable.sqlite3 file.
   If you used a prior single-EXE version, look instead at:
   %LOCALAPPDATA%\APMHSS_Time_Table\school_timetable.sqlite3
5. Double-click BUILD_SINGLE_EXE.bat. Internet may be needed once.
6. When build completes, open SINGLE_EXE_READY.
7. Copy ONLY APMHSS_Time_Table.exe to the new PC and run it.
8. Log in using your SAME username and password (no setup again).

The school photo, logo and icon are built into the EXE.
You do NOT need to carry a data folder to the new PC for the initial transfer.

CAUTIONS
--------
- Do not delete the saved local database while the application is in use.
- On a PC where APMHSS already has a database, the app keeps that PC's
  existing database instead of replacing it with the embedded snapshot.
- Anyone who receives the EXE may extract its embedded database. Protect
  sensitive student and staff data and only share with authorized users.
- The EXE must be built on Windows; this ZIP contains builder/source code,
  not a precompiled Windows EXE.

WINERROR 32 FIX (October 2026)
-----------------------------
The builder now closes SQLite connections and does not try to delete a temporary
snapshot immediately after PyInstaller finishes. This prevents a file-in-use
error that appeared AFTER the EXE was successfully compiled.
The staging folder _BUILD_SNAPSHOT_DO_NOT_TRANSFER is only used to build; do not
copy or share it. Copy only SINGLE_EXE_READY\APMHSS_Time_Table.exe.
