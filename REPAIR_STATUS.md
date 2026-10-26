# Partial repair and Windows build

This branch adds automatic verified backups, optional AES-GCM database encryption,
persistent login throttling and one-use account recovery codes. It uses the
repository interface; the uploaded newer interface needs missing Edification and
layout modules and is not included in this build.

The Windows EXE workflow runs tests and checks packaged startup before uploading
APMHSS-Windows-EXE. It contains no school database or account passwords. Existing
local data is retained; a new computer requires first-run account setup. Download
the artifact from the successful Windows EXE run on this branch.

## Storage and recovery

Install requirements before running Python source. Set APMHSS_BACKUP_DIR to a
separate disk for protection against loss of the primary disk. Automatic backups
run on startup, after changed commits at 15-minute intervals, and on clean close;
the most recent 28 are retained. Backups follow the database encryption mode.

Encryption is opt-in. Close all instances, generate a random base64 32-byte key
with `python storage_security.py generate-key`, store it securely, and set
APMHSS_DATA_KEY before `python storage_security.py encrypt PATH_TO_DATABASE`.
Set the same key when running the app. Existing plaintext backups remain plaintext.
Never commit the key or databases. Losing the key prevents recovery of encrypted
data. Encryption cannot protect against someone controlling the running OS account.

After login, use Recovery Codes and confirm your password. Save codes offline.
Each works once through Forgot password / recovery code. Generating a new set
invalidates the old set. Without previously issued codes, this recovery path
cannot reset a forgotten password. Five failed attempts lock that account for
15 minutes, including across application restarts.

## Outstanding work

Shared computer synchronization and server-enforced teacher privacy, school
holiday updates, preservation of manual Edification cycle edits, and approval of
class timings remain unfinished. The existing scheduling algorithm still relies
on correct teacher assignments. The EXE startup check does not verify printing,
display scaling, antivirus behavior, or every supported Windows version.
This is not a complete fix of all ten original drawbacks.
