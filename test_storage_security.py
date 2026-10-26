import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from timetable_core import Store
from storage_security import MAGIC, StorageSecurityError, secure_login

class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'school.db'
        self.env = patch.dict(os.environ, {'APMHSS_DATA_KEY': '', 'APMHSS_BACKUP_DIR': str(Path(self.tmp.name)/'backups')})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
    def test_lock_survives_restart_and_expires(self):
        s = Store(self.path)
        s.add_user('admin', 'Password123', 'admin')
        for _ in range(5):
            self.assertIsNone(secure_login(s, 'admin', 'wrong', now=1000))
        s.close()
        s = Store(self.path)
        self.assertIsNone(secure_login(s, 'admin', 'Password123', now=1001))
        self.assertEqual(secure_login(s, 'admin', 'Password123', now=1901), 'admin')
        s.close()
    def test_recovery_is_one_use_and_stored_hashed(self):
        s = Store(self.path)
        s.add_user('admin', 'Password123', 'admin')
        code = s.recovery_codes('admin')[0]
        self.assertNotIn(code, self.path.read_bytes().decode('latin1'))
        self.assertTrue(s.recover('admin', code, 'NewPassword123'))
        self.assertFalse(s.recover('admin', code, 'Another123'))
        self.assertEqual(s.login('admin', 'NewPassword123'), 'admin')
        s.close()
    def test_encrypted_reopen_backup_wrong_key_and_lock(self):
        os.environ['APMHSS_DATA_KEY'] = base64.urlsafe_b64encode(os.urandom(32)).decode()
        s = Store(self.path)
        s.add_user('admin', 'Password123', 'admin')
        with self.assertRaises(StorageSecurityError):
            Store(self.path)
        backup = self.path.with_name('copy.db')
        s.backup(backup)
        self.assertTrue(backup.read_bytes().startswith(MAGIC))
        s.close()
        s = Store(self.path)
        self.assertEqual(s.login('admin', 'Password123'), 'admin')
        s.close()
        original = self.path.read_bytes()
        os.environ['APMHSS_DATA_KEY'] = base64.urlsafe_b64encode(os.urandom(32)).decode()
        with self.assertRaises(StorageSecurityError):
            Store(self.path)
        self.assertEqual(self.path.read_bytes(), original)
    def test_automatic_backup_can_restore_account(self):
        s = Store(self.path)
        s.add_user('admin', 'Password123', 'admin')
        s.close()
        files = sorted((Path(self.tmp.name)/'backups').glob('*'), key=lambda p:p.stat().st_mtime)
        self.assertTrue(files)
        restored = Store(files[-1])
        self.assertEqual(restored.login('admin', 'Password123'), 'admin')
        restored.close()
