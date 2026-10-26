"""Durable local storage, backups and account recovery.

APMHSS_DATA_KEY must be a base64-encoded random 32-byte key. Keep it outside
teacher computers and the database/backup folders. Encryption protects stolen
files, not a user who controls the running application or its operating account.
A shared service for teachers on separate computers is not implemented.

Existing plaintext databases require the explicit ``encrypt`` command. Keep a
copy of the key offline: passwords and recovery codes cannot decrypt a database.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
import warnings

MAGIC = b'APMHSS-ENC-1\x00'
BACKUP_INTERVAL = 15 * 60
BACKUP_RETENTION = 28
LOGIN_LIMIT = 5
LOCK_SECONDS = 15 * 60
KDF_ITERATIONS = 310_000


class StorageSecurityError(ValueError):
    """A configured storage protection could not be applied safely."""


def data_key(value=None):
    value = os.environ.get('APMHSS_DATA_KEY', '') if value is None else value
    if not value:
        return None
    try:
        key = base64.b64decode(value, altchars=b'-_', validate=True)
    except (ValueError, TypeError) as exc:
        raise StorageSecurityError('APMHSS_DATA_KEY must be a base64-encoded 32-byte key.') from exc
    if len(key) != 32:
        raise StorageSecurityError('APMHSS_DATA_KEY must decode to exactly 32 bytes.')
    return key


def _cipher(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise StorageSecurityError('Install the cryptography package to use encrypted storage.') from exc
    return AESGCM(key)


def _encrypt(raw, key):
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + _cipher(key).encrypt(nonce, raw, MAGIC)


def _decrypt(raw, key):
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 28:
        raise StorageSecurityError('Not a supported encrypted database.')
    if key is None:
        raise StorageSecurityError('This database is encrypted. Supply its APMHSS_DATA_KEY.')
    from cryptography.exceptions import InvalidTag
    try:
        return _cipher(key).decrypt(raw[len(MAGIC):len(MAGIC)+12], raw[len(MAGIC)+12:], MAGIC)
    except InvalidTag as exc:
        raise StorageSecurityError('The database key is wrong or the encrypted file is damaged.') from exc


def _atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class _ProcessLock:
    def __init__(self, path):
        self.stream = open(str(path) + '.lock', 'a+b')
        try:
            if os.fstat(self.stream.fileno()).st_size == 0:
                self.stream.write(b'0')
                self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise StorageSecurityError('This encrypted database is already open. Use one shared server process.') from exc

    def close(self):
        if self.stream.closed:
            return
        if os.name == 'nt':
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def _verified_snapshot(connection):
    if connection.in_transaction:
        raise StorageSecurityError('Commit or roll back changes before taking a backup.')
    with closing(sqlite3.connect(':memory:')) as snapshot:
        connection.backup(snapshot)
        if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise StorageSecurityError('Database integrity check failed; no backup was written.')
        return snapshot.serialize()


class DurableConnection(sqlite3.Connection):
    """Persists encrypted databases and checks automatic backups after commits.

    Encrypted stores deliberately support one process/connection. The shared
    server should serialize requests through that connection, never open a
    connection for each request. Normal SQLite stores retain SQLite locking.
    """
    storage_path = None
    storage_key = None
    _process_lock = None
    _backups_enabled = False
    _last_backup = 0
    _last_backup_changes = -1
    _closed = False
    backup_error = None

    def _after_commit(self):
        if self.storage_key is not None:
            # serialize() never writes a plaintext temporary file.
            _atomic_write(self.storage_path, _encrypt(self.serialize(), self.storage_key))
        if self._backups_enabled:
            self.automatic_backup()

    def commit(self):
        super().commit()
        self._after_commit()

    def __exit__(self, exception_type, exception, traceback):
        result = super().__exit__(exception_type, exception, traceback)
        if exception_type is None:
            self._after_commit()
        return result

    def automatic_backup(self, force=False):
        now = time.time()
        changed = self.total_changes != self._last_backup_changes
        if not force and (not changed or now - self._last_backup < BACKUP_INTERVAL):
            return None
        directory = Path(os.environ.get('APMHSS_BACKUP_DIR') or self.storage_path.parent / 'backups')
        prefix = self.storage_path.name + '.'
        extension = '.apmhss' if self.storage_key is not None else '.sqlite3'
        destination = directory / (prefix + time.strftime('%Y%m%dT%H%M%S', time.gmtime(now))
                                   + '-' + secrets.token_hex(4) + extension)
        try:
            backup_database(self, destination)
            # Only prune our own verified backups after successfully saving the new one.
            candidates = sorted(directory.glob(prefix + '*' + extension), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in candidates[BACKUP_RETENTION:]:
                old.unlink()
            self._last_backup = now
            self._last_backup_changes = self.total_changes
            self.backup_error = None
            return destination
        except (OSError, sqlite3.Error, StorageSecurityError) as exc:
            self.backup_error = str(exc)
            warnings.warn('Automatic backup failed: ' + str(exc), RuntimeWarning, stacklevel=2)
            return None

    def close(self):
        if self._closed:
            return
        try:
            # Closing must not commit an unfinished/failed transaction.
            if self.in_transaction:
                self.rollback()
            if self._backups_enabled and self.total_changes != self._last_backup_changes:
                self.automatic_backup(force=True)
        finally:
            super().close()
            self._closed = True
            if self._process_lock is not None:
                self._process_lock.close()


def connect_database(path, timeout=15, **kwargs):
    """Open an encrypted store when a key is configured; otherwise legacy SQLite.

    Never silently convert existing plaintext or fall back after a key error.
    Use check_same_thread=False only with a caller-provided serialization lock.
    """
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = data_key()
    header = b''
    if path.exists():
        with path.open('rb') as source:
            header = source.read(len(MAGIC))
    if header.startswith(MAGIC) and key is None:
        raise StorageSecurityError('This database is encrypted. Supply its APMHSS_DATA_KEY.')
    if key is not None and path.exists() and not header.startswith(MAGIC):
        raise StorageSecurityError('Existing database is plaintext. Close the app and run: python storage_security.py encrypt PATH')
    lock = _ProcessLock(path) if key is not None else None
    connection = None
    try:
        connection = sqlite3.connect(':memory:' if key else str(path), timeout=timeout,
                                     factory=DurableConnection, **kwargs)
        connection.storage_path = path
        connection.storage_key = key
        connection._process_lock = lock
        if key is not None:
            connection.execute('PRAGMA temp_store=MEMORY')
            if path.exists():
                connection.deserialize(_decrypt(path.read_bytes(), key))
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise StorageSecurityError('Encrypted database integrity check failed.')
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        elif lock is not None:
            lock.close()
        raise


def configure_backups(connection, path=None):
    """Call once after schema migrations, before opening the application's UI."""
    if not isinstance(connection, DurableConnection):
        raise TypeError('Use connect_database() before configure_backups().')
    if path is not None:
        connection.storage_path = Path(path).resolve()
    connection.commit()
    connection._backups_enabled = True
    connection.automatic_backup(force=True)


def backup_database(connection, destination):
    destination = Path(destination).expanduser().resolve()
    if getattr(connection, 'storage_path', None) == destination:
        raise StorageSecurityError('Choose a different path for the backup.')
    raw = _verified_snapshot(connection)
    key = getattr(connection, 'storage_key', None)
    if key is not None:
        raw = _encrypt(raw, key)
        # Verify authentication of the exact bytes being written.
        _decrypt(raw, key)
    _atomic_write(destination, raw)
    return destination


def encrypt_database(path):
    """Explicit in-place migration. Close every app/server instance first."""
    key = data_key()
    if key is None:
        raise StorageSecurityError('Set APMHSS_DATA_KEY before encrypting a database.')
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise StorageSecurityError('The database to encrypt does not exist.')
    lock = _ProcessLock(path)
    try:
        if path.read_bytes().startswith(MAGIC):
            _decrypt(path.read_bytes(), key)
            return
        with closing(sqlite3.connect(str(path), timeout=1)) as source:
            source.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            raw = _verified_snapshot(source)
        encrypted = _encrypt(raw, key)
        _decrypt(encrypted, key)
        _atomic_write(path, encrypted)
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
    finally:
        lock.close()


def security_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS account_attempts (
      username TEXT NOT NULL, purpose TEXT NOT NULL,
      failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
      PRIMARY KEY(username,purpose));
    CREATE TABLE IF NOT EXISTS account_recovery (
      username TEXT NOT NULL, code_hash TEXT NOT NULL,
      PRIMARY KEY(username,code_hash));
    ''')
    db.commit()


def _normal_username(username):
    return str(username).strip().lower()


def _allowed(db, username, purpose, now):
    row = db.execute('SELECT locked_until FROM account_attempts WHERE username=? AND purpose=?',
                     (username, purpose)).fetchone()
    return row is None or row[0] <= now


def _failure(db, username, purpose, now):
    row = db.execute('SELECT failures,locked_until FROM account_attempts WHERE username=? AND purpose=?',
                     (username, purpose)).fetchone()
    failures = (row[0] if row and not row[1] else 0) + 1
    locked_until = now + LOCK_SECONDS if failures >= LOGIN_LIMIT else 0
    db.execute('INSERT OR REPLACE INTO account_attempts VALUES(?,?,?,?)',
               (username, purpose, failures, locked_until))


def secure_login(store, username, password, *, now=None):
    """Return the role or None, with restart-persistent per-account throttling."""
    username = _normal_username(username)
    now = time.time() if now is None else now
    db = store.db
    # One write transaction makes simultaneous failures count accurately.
    db.execute('BEGIN IMMEDIATE')
    try:
        if not _allowed(db, username, 'login', now):
            db.rollback()
            return None
        row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        salt = row['salt'] if row else b'0' * 16
        iterations = row['kdf_iterations'] if row else KDF_ITERATIONS
        digest = hashlib.pbkdf2_hmac('sha256', str(password).encode(), salt, iterations)
        valid = row is not None and hmac.compare_digest(digest, row['password_hash'])
        if valid:
            db.execute('DELETE FROM account_attempts WHERE username=? AND purpose=?', (username, 'login'))
        else:
            _failure(db, username, 'login', now)
        db.commit()
        return row['role'] if valid else None
    except Exception:
        db.rollback()
        raise


def issue_recovery_codes(store, username, count=8):
    """Replace recovery codes. Caller must authenticate the account or be admin."""
    username = _normal_username(username)
    if not store.db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
        raise StorageSecurityError('Account not found.')
    if not isinstance(count, int) or not 1 <= count <= 20:
        raise StorageSecurityError('Recovery code count must be between 1 and 20.')
    codes = [secrets.token_hex(16) for _ in range(count)]
    with store.db:
        store.db.execute('DELETE FROM account_recovery WHERE username=?', (username,))
        store.db.executemany('INSERT INTO account_recovery VALUES(?,?)',
                             [(username, hashlib.sha256(code.encode()).hexdigest()) for code in codes])
    return codes


def recover_account(store, username, code, new_password, *, now=None):
    """Consume one recovery code and set a password in a single transaction."""
    username = _normal_username(username)
    if len(new_password) < 8 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):
        raise StorageSecurityError('Use 8+ characters, with letters and numbers.')
    now = time.time() if now is None else now
    db = store.db
    db.execute('BEGIN IMMEDIATE')
    try:
        if not _allowed(db, username, 'recovery', now):
            db.rollback()
            return False
        code_hash = hashlib.sha256(str(code).strip().lower().encode()).hexdigest()
        row = db.execute('SELECT 1 FROM account_recovery WHERE username=? AND code_hash=?',
                         (username, code_hash)).fetchone()
        exists = db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone()
        if not row or not exists:
            _failure(db, username, 'recovery', now)
            db.commit()
            return False
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', new_password.encode(), salt, KDF_ITERATIONS)
        db.execute('UPDATE users SET salt=?,password_hash=?,kdf_iterations=? WHERE username=?',
                   (salt, digest, KDF_ITERATIONS, username))
        db.execute('DELETE FROM account_recovery WHERE username=? AND code_hash=?', (username, code_hash))
        db.execute('DELETE FROM account_attempts WHERE username=?', (username,))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('generate-key', 'encrypt'))
    parser.add_argument('path', nargs='?')
    args = parser.parse_args()
    if args.action == 'generate-key':
        print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    elif not args.path:
        parser.error('encrypt requires a database path; close every app/server first')
    else:
        encrypt_database(args.path)
        print('Encrypted database in place. Keep the key offline; remove old plaintext copies securely.')


if __name__ == '__main__':
    main()
