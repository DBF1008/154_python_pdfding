import base64
from pathlib import Path
from unittest import mock

from backup import service
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

    @mock.patch('backup.service.Fernet')
    def test_validate_encryption_key_valid(self, mock_fernet_cls):
        mock_fernet_instance = mock.Mock()
        mock_fernet_instance.decrypt.return_value = b'decrypted'
        mock_fernet_cls.return_value = mock_fernet_instance

        result = service.validate_encryption_key(b'key', b'sample_data')

        self.assertTrue(result)
        mock_fernet_cls.assert_called_with(b'key')
        mock_fernet_instance.decrypt.assert_called_with(b'sample_data')

    @mock.patch('backup.service.Fernet')
    def test_validate_encryption_key_invalid(self, mock_fernet_cls):
        from cryptography.fernet import InvalidToken

        mock_fernet_instance = mock.Mock()
        mock_fernet_instance.decrypt.side_effect = InvalidToken
        mock_fernet_cls.return_value = mock_fernet_instance

        result = service.validate_encryption_key(b'key', b'sample_data')

        self.assertFalse(result)

    def test_is_fernet_ciphertext_true(self):
        # Fernet tokens start with version byte 0x80, which base64-encodes to 'gA'
        # Build a plausible Fernet-like token: 'gA' prefix + padding to exceed 57 bytes
        sample = b'gA' + b'A' * 55

        self.assertTrue(service.is_fernet_ciphertext(sample))

    def test_is_fernet_ciphertext_false(self):
        # Regular PDF header — not Fernet ciphertext
        sample = b'%PDF-1.4 this is a regular PDF file header with enough length to pass the size check!!'

        self.assertFalse(service.is_fernet_ciphertext(sample))

    def test_is_fernet_ciphertext_too_short(self):
        sample = b'gAAA'

        self.assertFalse(service.is_fernet_ciphertext(sample))
