import base64
from pathlib import Path
from unittest import mock

from backup import service
from cryptography.fernet import Fernet
from django.test import TestCase


class TestEncryption(TestCase):
    mock_pbk_object = mock.Mock()
    mock_pbk_object.derive = lambda x: b'generated_key'

    mock_fernet_object = mock.Mock()
    mock_fernet_object.encrypt = lambda x: x + b'encrypted'
    mock_fernet_object.decrypt = lambda x: x + b'decrypted'

    def test_get_encryption_key_disabled(self):
        self.assertEqual(service.get_encryption_key(False, 'pw', 'salt'), None)

    @mock.patch('backup.service.generate_encryption_key', return_value=b'key')
    def test_get_encryption_key_enabled(self, mock_generate_encryption_key):
        self.assertEqual(service.get_encryption_key(True, 'pw', 'salt'), b'key')

    @mock.patch('backup.service.SHA256', return_value='sha256')
    @mock.patch('backup.service.PBKDF2HMAC', return_value=mock_pbk_object)
    def test_generate_encryption_key(self, mock_pbkdf2hmac, mock_sha256):
        generated_key = service.generate_encryption_key('pw', 'salt')

        mock_pbkdf2hmac.assert_called_with(
            algorithm='sha256',
            length=32,
            salt=b'salt',
            iterations=1000000,
        )

        self.assertEqual(generated_key, base64.urlsafe_b64encode(b'generated_key'))

    @mock.patch('backup.service.Fernet', return_value=mock_fernet_object)
    def test_encrypt_file(self, mock_fernet):
        parent_path = Path(__file__).parent
        tmp_file_path = parent_path / 'tmp'

        service.encrypt_file(b'key', parent_path / '__init__.py', tmp_file_path)

        with open(tmp_file_path, 'rb') as file:
            tmp_file_contents = file.read()

        # delete the tmp file
        tmp_file_path.unlink()

        self.assertEqual(tmp_file_contents, b'"""some content for encryption test"""\nencrypted')

    @mock.patch('backup.service.Fernet', return_value=mock_fernet_object)
    def test_decrypt_file(self, mock_fernet):
        parent_path = Path(__file__).parent
        tmp_file_path = parent_path / 'tmp'

        service.decrypt_file(b'key', parent_path / '__init__.py', tmp_file_path)

        with open(tmp_file_path, 'rb') as file:
            tmp_file_contents = file.read()

        # delete the tmp file
        tmp_file_path.unlink()

        self.assertEqual(tmp_file_contents, b'"""some content for encryption test"""\ndecrypted')


class TestRecoveryHelpers(TestCase):
    @staticmethod
    def write_tmp(data: bytes) -> Path:
        tmp_path = Path(__file__).parent / 'tmp_recovery'
        with open(tmp_path, 'wb') as tmp_file:
            tmp_file.write(data)

        return tmp_path

    def test_looks_like_fernet_token_true(self):
        token = Fernet(Fernet.generate_key()).encrypt(b'some secret content')

        self.assertTrue(service.looks_like_fernet_token(token))

    def test_looks_like_fernet_token_false(self):
        # not valid base64 / not a token structure
        self.assertFalse(service.looks_like_fernet_token(b'%PDF-1.7 not a token'))
        self.assertFalse(service.looks_like_fernet_token(service.SQLITE_HEADER + b'rest of the db'))

    def test_verify_sqlite_file_true(self):
        tmp_path = self.write_tmp(service.SQLITE_HEADER + b'rest of a sqlite database')

        try:
            self.assertTrue(service.verify_sqlite_file(tmp_path))
        finally:
            tmp_path.unlink()

    def test_verify_sqlite_file_false(self):
        tmp_path = self.write_tmp(b'this is not a sqlite database')

        try:
            self.assertFalse(service.verify_sqlite_file(tmp_path))
        finally:
            tmp_path.unlink()

    def test_detect_and_validate_encryption_plaintext(self):
        tmp_path = self.write_tmp(b'%PDF-1.7 plaintext backup')

        try:
            self.assertEqual(service.detect_and_validate_encryption(tmp_path, None), 'plaintext')
        finally:
            tmp_path.unlink()

    def test_detect_and_validate_encryption_encrypted(self):
        key = Fernet.generate_key()
        tmp_path = self.write_tmp(Fernet(key).encrypt(b'encrypted backup'))

        try:
            self.assertEqual(service.detect_and_validate_encryption(tmp_path, key), 'encrypted')
        finally:
            tmp_path.unlink()

    def test_detect_and_validate_encryption_wrong_key(self):
        tmp_path = self.write_tmp(Fernet(Fernet.generate_key()).encrypt(b'encrypted backup'))

        try:
            with self.assertRaises(service.RecoveryPreflightError):
                service.detect_and_validate_encryption(tmp_path, Fernet.generate_key())
        finally:
            tmp_path.unlink()

    def test_detect_and_validate_encryption_enabled_but_plaintext(self):
        tmp_path = self.write_tmp(b'%PDF-1.7 plaintext backup, not a token')

        try:
            with self.assertRaises(service.RecoveryPreflightError):
                service.detect_and_validate_encryption(tmp_path, Fernet.generate_key())
        finally:
            tmp_path.unlink()

    def test_detect_and_validate_encryption_disabled_but_encrypted(self):
        tmp_path = self.write_tmp(Fernet(Fernet.generate_key()).encrypt(b'encrypted backup'))

        try:
            with self.assertRaises(service.RecoveryPreflightError):
                service.detect_and_validate_encryption(tmp_path, None)
        finally:
            tmp_path.unlink()
