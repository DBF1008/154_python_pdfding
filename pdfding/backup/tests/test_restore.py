from pathlib import Path
from unittest import mock

from cryptography.fernet import InvalidToken

from backup import restore
from backup.restore import (
    BackupManifest,
    ConflictReport,
    apply_staged_restore,
    build_manifest,
    check_minio_connection,
    cleanup_staging,
    create_safety_backup,
    detect_conflicts,
    download_to_staging,
    run_pre_check,
    validate_encryption_against_backup,
)
from django.test import TestCase


class TestCheckMinioConnection(TestCase):
    def test_connection_success(self):
        mock_client = mock.Mock()
        mock_client.bucket_exists.return_value = True

        reachable, bucket_exists, errors = check_minio_connection(mock_client, 'pdfding')

        self.assertTrue(reachable)
        self.assertTrue(bucket_exists)
        self.assertEqual(errors, [])
        mock_client.bucket_exists.assert_called_with('pdfding')

    def test_bucket_missing(self):
        mock_client = mock.Mock()
        mock_client.bucket_exists.return_value = False

        reachable, bucket_exists, errors = check_minio_connection(mock_client, 'pdfding')

        self.assertTrue(reachable)
        self.assertFalse(bucket_exists)
        self.assertEqual(len(errors), 1)
        self.assertIn('does not exist', errors[0])

    def test_connection_failure(self):
        mock_client = mock.Mock()
        mock_client.bucket_exists.side_effect = Exception('connection timeout')

        reachable, bucket_exists, errors = check_minio_connection(mock_client, 'pdfding')

        self.assertFalse(reachable)
        self.assertFalse(bucket_exists)
        self.assertEqual(len(errors), 1)
        self.assertIn('Cannot connect to MinIO', errors[0])


class TestBuildManifest(TestCase):
    def test_with_db_and_media(self):
        mock_obj_1 = mock.Mock()
        mock_obj_1.object_name = 'backup.sqlite3'
        mock_obj_1.size = 1024

        mock_obj_2 = mock.Mock()
        mock_obj_2.object_name = '1/default/pdf_1.pdf'
        mock_obj_2.size = 2048

        mock_obj_3 = mock.Mock()
        mock_obj_3.object_name = '1/default/qr/qr_1.svg'
        mock_obj_3.size = 512

        mock_client = mock.Mock()
        mock_client.list_objects.return_value = [mock_obj_1, mock_obj_2, mock_obj_3]

        manifest = build_manifest(mock_client, 'pdfding', 'backup.sqlite3')

        self.assertEqual(manifest.db_object_name, 'backup.sqlite3')
        self.assertEqual(manifest.media_object_names, ['1/default/pdf_1.pdf', '1/default/qr/qr_1.svg'])
        self.assertEqual(manifest.total_size_bytes, 3584)
        self.assertEqual(manifest.object_count, 3)

    def test_empty_bucket_raises(self):
        mock_client = mock.Mock()
        mock_client.list_objects.return_value = []

        with self.assertRaises(ValueError) as ctx:
            build_manifest(mock_client, 'pdfding', 'backup.sqlite3')

        self.assertIn('empty', str(ctx.exception))

    def test_no_db_name_postgres_mode(self):
        mock_obj = mock.Mock()
        mock_obj.object_name = '1/default/pdf_1.pdf'
        mock_obj.size = 1024

        mock_client = mock.Mock()
        mock_client.list_objects.return_value = [mock_obj]

        manifest = build_manifest(mock_client, 'pdfding', None)

        self.assertIsNone(manifest.db_object_name)
        self.assertEqual(manifest.media_object_names, ['1/default/pdf_1.pdf'])
        self.assertEqual(manifest.object_count, 1)


class TestValidateEncryption(TestCase):
    @mock.patch('builtins.open', mock.mock_open(read_data=b'%PDF-1.4 regular pdf content'))
    @mock.patch('backup.restore.Path.unlink')
    def test_no_key_unencrypted_backup(self, mock_unlink):
        mock_client = mock.Mock()

        valid, needed, errors = validate_encryption_against_backup(mock_client, 'pdfding', 'test.pdf', None)

        self.assertTrue(valid)
        self.assertFalse(needed)
        self.assertEqual(errors, [])

    @mock.patch('backup.restore.is_fernet_ciphertext', return_value=True)
    @mock.patch('builtins.open', mock.mock_open(read_data=b'gA' + b'A' * 60))
    @mock.patch('backup.restore.Path.unlink')
    def test_no_key_encrypted_backup(self, mock_unlink, mock_is_fernet):
        mock_client = mock.Mock()

        valid, needed, errors = validate_encryption_against_backup(mock_client, 'pdfding', 'test.pdf', None)

        self.assertFalse(valid)
        self.assertTrue(needed)
        self.assertEqual(len(errors), 1)
        self.assertIn('encrypted', errors[0])

    @mock.patch('backup.restore.validate_encryption_key', return_value=True)
    @mock.patch('builtins.open', mock.mock_open(read_data=b'encrypted_data'))
    @mock.patch('backup.restore.Path.unlink')
    def test_correct_key(self, mock_unlink, mock_validate):
        mock_client = mock.Mock()

        valid, needed, errors = validate_encryption_against_backup(mock_client, 'pdfding', 'test.pdf', b'key')

        self.assertTrue(valid)
        self.assertTrue(needed)
        self.assertEqual(errors, [])

    @mock.patch('backup.restore.validate_encryption_key', return_value=False)
    @mock.patch('builtins.open', mock.mock_open(read_data=b'encrypted_data'))
    @mock.patch('backup.restore.Path.unlink')
    def test_wrong_key(self, mock_unlink, mock_validate):
        mock_client = mock.Mock()

        valid, needed, errors = validate_encryption_against_backup(mock_client, 'pdfding', 'test.pdf', b'key')

        self.assertFalse(valid)
        self.assertTrue(needed)
        self.assertEqual(len(errors), 1)
        self.assertIn('password', errors[0].lower())


class TestDetectConflicts(TestCase):
    def test_no_conflicts(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            media_root = tmp_path / 'media'
            media_root.mkdir()
            db_path = tmp_path / 'db.sqlite3'

            manifest = BackupManifest(
                db_object_name='backup.sqlite3',
                media_object_names=['1/default/pdf_1.pdf'],
                total_size_bytes=1024,
                object_count=2,
            )

            conflicts = detect_conflicts(manifest, db_path, media_root)

            self.assertFalse(conflicts.db_will_overwrite)
            self.assertEqual(conflicts.media_conflicts, [])
            self.assertFalse(conflicts.has_conflicts)

    def test_db_conflict(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            media_root = tmp_path / 'media'
            media_root.mkdir()
            db_path = tmp_path / 'db.sqlite3'
            db_path.touch()

            manifest = BackupManifest(
                db_object_name='backup.sqlite3',
                media_object_names=[],
                total_size_bytes=1024,
                object_count=1,
            )

            conflicts = detect_conflicts(manifest, db_path, media_root)

            self.assertTrue(conflicts.db_will_overwrite)
            self.assertTrue(conflicts.has_conflicts)

    def test_media_conflicts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            media_root = tmp_path / 'media'
            media_root.mkdir()
            db_path = tmp_path / 'db.sqlite3'

            # Create an existing media file
            existing = media_root / '1' / 'default'
            existing.mkdir(parents=True)
            (existing / 'pdf_1.pdf').touch()

            manifest = BackupManifest(
                db_object_name=None,
                media_object_names=['1/default/pdf_1.pdf', '1/default/pdf_2.pdf'],
                total_size_bytes=2048,
                object_count=2,
            )

            conflicts = detect_conflicts(manifest, db_path, media_root)

            self.assertFalse(conflicts.db_will_overwrite)
            self.assertEqual(conflicts.media_conflicts, ['1/default/pdf_1.pdf'])
            self.assertTrue(conflicts.has_conflicts)


class TestRunPreCheck(TestCase):
    @mock.patch('backup.restore.detect_conflicts')
    @mock.patch('backup.restore.validate_encryption_against_backup', return_value=(True, True, []))
    @mock.patch('backup.restore.build_manifest')
    @mock.patch('backup.restore.check_minio_connection', return_value=(True, True, []))
    def test_full_pre_check_success(self, mock_conn, mock_manifest, mock_enc, mock_conflicts):
        manifest = BackupManifest(
            db_object_name='backup.sqlite3',
            media_object_names=['1/default/pdf_1.pdf'],
            total_size_bytes=3072,
            object_count=2,
        )
        mock_manifest.return_value = manifest
        mock_conflicts.return_value = ConflictReport(db_will_overwrite=False, media_conflicts=[])

        result = run_pre_check(
            minio_client=mock.Mock(),
            bucket_name='pdfding',
            db_backup_name='backup.sqlite3',
            db_live_path=Path('/tmp/db.sqlite3'),
            media_root=Path('/tmp/media'),
            encryption_key=b'key',
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.manifest)
        self.assertEqual(result.manifest.object_count, 2)

    @mock.patch('backup.restore.build_manifest')
    @mock.patch('backup.restore.check_minio_connection', return_value=(False, False, ['Cannot connect']))
    def test_stops_on_connection_failure(self, mock_conn, mock_manifest):
        result = run_pre_check(
            minio_client=mock.Mock(),
            bucket_name='pdfding',
            db_backup_name='backup.sqlite3',
            db_live_path=Path('/tmp/db.sqlite3'),
            media_root=Path('/tmp/media'),
            encryption_key=None,
        )

        self.assertFalse(result.ok)
        self.assertFalse(result.minio_reachable)
        mock_manifest.assert_not_called()

    @mock.patch('backup.restore.build_manifest', side_effect=ValueError('Backup bucket is empty'))
    @mock.patch('backup.restore.check_minio_connection', return_value=(True, True, []))
    def test_stops_on_empty_bucket(self, mock_conn, mock_manifest):
        result = run_pre_check(
            minio_client=mock.Mock(),
            bucket_name='pdfding',
            db_backup_name='backup.sqlite3',
            db_live_path=Path('/tmp/db.sqlite3'),
            media_root=Path('/tmp/media'),
            encryption_key=None,
        )

        self.assertFalse(result.ok)
        self.assertIn('empty', result.errors[0])


class TestCreateSafetyBackup(TestCase):
    def test_backup_created(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / 'db.sqlite3'
            db_path.write_text('fake db content')
            safety_path = tmp_path / 'db.safety_backup'

            result = create_safety_backup(db_path, safety_path)

            self.assertTrue(result)
            self.assertTrue(safety_path.exists())
            self.assertEqual(safety_path.read_text(), 'fake db content')

    def test_no_existing_db(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / 'db.sqlite3'  # doesn't exist
            safety_path = tmp_path / 'db.safety_backup'

            result = create_safety_backup(db_path, safety_path)

            self.assertFalse(result)
            self.assertFalse(safety_path.exists())


class TestDownloadSingleFile(TestCase):
    @mock.patch('backup.restore.Path.unlink')
    @mock.patch('backup.restore.decrypt_file')
    def test_with_encryption(self, mock_decrypt, mock_unlink):
        mock_client = mock.Mock()
        target_path = Path('path/to/file.pdf')

        restore._download_single_file(mock_client, 'pdfding', 'file.pdf', target_path, b'key')

        mock_client.fget_object.assert_called_once()
        mock_decrypt.assert_called_once()
        mock_unlink.assert_called_once()

    def test_no_encryption(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'file.pdf'
            mock_client = mock.Mock()

            restore._download_single_file(mock_client, 'pdfding', 'file.pdf', target, None)

            mock_client.fget_object.assert_called_with('pdfding', 'file.pdf', str(target))

    @mock.patch('backup.restore.decrypt_file', side_effect=InvalidToken())
    def test_decrypt_failure(self, mock_decrypt):
        import tempfile

        mock_client = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'file.pdf'

            with self.assertRaises(ValueError) as ctx:
                restore._download_single_file(mock_client, 'pdfding', 'file.pdf', target, b'key')

            self.assertIn('Decryption failed', str(ctx.exception))


class TestDownloadToStaging(TestCase):
    @mock.patch('backup.restore._download_single_file')
    def test_download_all(self, mock_download):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / 'staging'

            manifest = BackupManifest(
                db_object_name='backup.sqlite3',
                media_object_names=['1/default/pdf_1.pdf', '1/default/qr/qr_1.svg'],
                total_size_bytes=3072,
                object_count=3,
            )

            downloaded = download_to_staging(mock.Mock(), 'pdfding', manifest, staging, b'key')

            self.assertEqual(len(downloaded), 3)
            self.assertEqual(mock_download.call_count, 3)
            self.assertTrue((staging / 'db').exists())
            self.assertTrue((staging / 'media').exists())

    @mock.patch('backup.restore._download_single_file', side_effect=ValueError('download failed'))
    def test_download_failure_raises(self, mock_download):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / 'staging'

            manifest = BackupManifest(
                db_object_name='backup.sqlite3',
                media_object_names=[],
                total_size_bytes=1024,
                object_count=1,
            )

            with self.assertRaises(ValueError):
                download_to_staging(mock.Mock(), 'pdfding', manifest, staging, None)


class TestApplyStagedRestore(TestCase):
    def test_db_switch_success(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Set up staging
            staging = tmp_path / 'staging'
            (staging / 'db').mkdir(parents=True)
            (staging / 'db' / 'backup.sqlite3').write_text('restored db')

            # Set up live db
            db_live = tmp_path / 'db.sqlite3'
            db_live.write_text('old db')

            media_root = tmp_path / 'media'
            media_root.mkdir()

            manifest = BackupManifest(
                db_object_name='backup.sqlite3',
                media_object_names=[],
                total_size_bytes=1024,
                object_count=1,
            )

            apply_staged_restore(staging, manifest, db_live, media_root)

            self.assertEqual(db_live.read_text(), 'restored db')
            # .bak should be cleaned up
            self.assertFalse((tmp_path / 'db.sqlite3.pre_restore_bak').exists())

    def test_db_rollback_on_failure(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            staging = tmp_path / 'staging'
            (staging / 'db').mkdir(parents=True)
            (staging / 'db' / 'backup.sqlite3').write_text('restored db')

            db_live = tmp_path / 'db.sqlite3'
            db_live.write_text('old db')

            media_root = tmp_path / 'media'
            media_root.mkdir()

            manifest = BackupManifest(
                db_object_name='backup.sqlite3',
                media_object_names=[],
                total_size_bytes=1024,
                object_count=1,
            )

            # Mock shutil.move to fail
            with mock.patch('backup.restore.shutil.move', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    apply_staged_restore(staging, manifest, db_live, media_root)

            # DB should be rolled back
            self.assertEqual(db_live.read_text(), 'old db')

    def test_media_move(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            staging = tmp_path / 'staging'
            media_staging = staging / 'media' / '1' / 'default'
            media_staging.mkdir(parents=True)
            (media_staging / 'pdf_1.pdf').write_text('restored pdf')

            media_root = tmp_path / 'media'
            media_root.mkdir()

            db_live = tmp_path / 'db.sqlite3'

            manifest = BackupManifest(
                db_object_name=None,
                media_object_names=['1/default/pdf_1.pdf'],
                total_size_bytes=1024,
                object_count=1,
            )

            apply_staged_restore(staging, manifest, db_live, media_root)

            self.assertEqual((media_root / '1' / 'default' / 'pdf_1.pdf').read_text(), 'restored pdf')


class TestCleanupStaging(TestCase):
    def test_cleanup_existing_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / 'staging'
            staging.mkdir()
            (staging / 'file.txt').write_text('data')

            cleanup_staging(staging)

            self.assertFalse(staging.exists())

    def test_cleanup_nonexistent_dir(self):
        # Should not raise
        cleanup_staging(Path('/nonexistent/path'))
