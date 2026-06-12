from pathlib import Path
from unittest import mock

from backup.management.commands import recover_data
from backup.management.commands.recover_data import Command, RecoveryPlan
from backup.service import RecoveryPreflightError
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

CMD = 'backup.management.commands.recover_data'


def make_object(name):
    mock_object = mock.Mock()
    mock_object.object_name = name

    return mock_object


def make_plan(conflicts=None, db_object='backup.sqlite3', media_objects=('pdf_1.pdf',)):
    media = list(media_objects)
    names = media + ([db_object] if db_object else [])

    return RecoveryPlan(
        object_names=names,
        media_objects=media,
        db_object=db_object,
        encryption_mode='encrypted',
        conflicts=list(conflicts or []),
    )


class TestRecoverDataHandle(TestCase):
    """Tests for the overall recovery orchestration: confirmation, dry run and the three phases."""

    @mock.patch(f'{CMD}.shutil.rmtree')
    @mock.patch.object(Command, 'commit')
    @mock.patch.object(Command, 'stage', return_value=(Path('media'), Path('db')))
    @mock.patch.object(Command, 'preflight', return_value=make_plan(conflicts=[]))
    @mock.patch(f'{CMD}.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_handle_runs_all_phases(
        self, mock_input, mock_get_key, mock_preflight, mock_stage, mock_commit, mock_rmtree
    ):
        call_command('recover_data')

        mock_preflight.assert_called_once()
        mock_stage.assert_called_once()
        mock_commit.assert_called_once_with(Path('media'), Path('db'))
        mock_rmtree.assert_called()

    @mock.patch.object(Command, 'stage')
    @mock.patch.object(Command, 'preflight')
    @mock.patch(f'{CMD}.get_encryption_key', return_value=None)
    @mock.patch('builtins.input', return_value='n')
    def test_handle_aborts_without_confirmation(self, mock_input, mock_get_key, mock_preflight, mock_stage):
        call_command('recover_data')

        mock_preflight.assert_not_called()
        mock_stage.assert_not_called()

    @mock.patch.object(Command, 'commit')
    @mock.patch.object(Command, 'stage')
    @mock.patch.object(Command, 'preflight', return_value=make_plan())
    @mock.patch(f'{CMD}.get_encryption_key', return_value=b'key')
    def test_handle_dry_run_skips_stage_and_commit(self, mock_get_key, mock_preflight, mock_stage, mock_commit):
        call_command('recover_data', '--dry-run')

        mock_preflight.assert_called_once()
        mock_stage.assert_not_called()
        mock_commit.assert_not_called()

    @mock.patch.object(Command, 'stage')
    @mock.patch.object(Command, 'preflight', side_effect=RecoveryPreflightError('boom'))
    @mock.patch(f'{CMD}.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_handle_preflight_error_raises_and_skips_stage(self, mock_input, mock_get_key, mock_preflight, mock_stage):
        with self.assertRaises(CommandError):
            call_command('recover_data')

        mock_stage.assert_not_called()

    @mock.patch.object(Command, 'stage')
    @mock.patch.object(Command, 'preflight', return_value=make_plan(conflicts=[Path('media/pdf_1.pdf')]))
    @mock.patch(f'{CMD}.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', side_effect=['y', 'n'])
    def test_handle_conflicts_declined(self, mock_input, mock_get_key, mock_preflight, mock_stage):
        call_command('recover_data')

        # confirmed the recovery but declined overwriting existing files -> nothing is staged
        mock_stage.assert_not_called()

    @mock.patch(f'{CMD}.shutil.rmtree')
    @mock.patch.object(Command, 'commit')
    @mock.patch.object(Command, 'stage', return_value=(Path('media'), Path('db')))
    @mock.patch.object(Command, 'preflight', return_value=make_plan(conflicts=[Path('media/pdf_1.pdf')]))
    @mock.patch(f'{CMD}.get_encryption_key', return_value=b'key')
    def test_handle_no_input_overwrites_without_prompt(
        self, mock_get_key, mock_preflight, mock_stage, mock_commit, mock_rmtree
    ):
        # --no-input implies --force, so the existing-files confirmation is skipped entirely
        call_command('recover_data', '--no-input')

        mock_stage.assert_called_once()
        mock_commit.assert_called_once()

    @mock.patch(f'{CMD}.shutil.rmtree')
    @mock.patch.object(Command, 'commit')
    @mock.patch.object(Command, 'stage', side_effect=RuntimeError('connection lost'))
    @mock.patch.object(Command, 'preflight', return_value=make_plan(conflicts=[]))
    @mock.patch(f'{CMD}.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_handle_staging_failure_cleans_up_and_skips_commit(
        self, mock_input, mock_get_key, mock_preflight, mock_stage, mock_commit, mock_rmtree
    ):
        with self.assertRaises(CommandError):
            call_command('recover_data')

        mock_commit.assert_not_called()
        mock_rmtree.assert_called()  # staging directory removed after the failure


class TestRecoverDataPreflight(TestCase):
    @mock.patch(f'{CMD}.Minio.bucket_exists', return_value=False)
    def test_preflight_missing_bucket(self, mock_bucket_exists):
        with self.assertRaises(RecoveryPreflightError):
            Command().preflight(b'key')

    @mock.patch(f'{CMD}.Minio.list_objects', return_value=[])
    @mock.patch(f'{CMD}.Minio.bucket_exists', return_value=True)
    def test_preflight_empty_bucket(self, mock_bucket_exists, mock_list_objects):
        with self.assertRaises(RecoveryPreflightError):
            Command().preflight(b'key')

    @mock.patch(f'{CMD}.Minio.list_objects', return_value=[make_object('pdf_1.pdf')])
    @mock.patch(f'{CMD}.Minio.bucket_exists', return_value=True)
    def test_preflight_missing_sqlite_backup(self, mock_bucket_exists, mock_list_objects):
        # sqlite engine is configured in the test settings, so a missing db backup must fail
        with self.assertRaises(RecoveryPreflightError):
            Command().preflight(b'key')

    @mock.patch(f'{CMD}.Path.exists', return_value=False)
    @mock.patch(f'{CMD}.detect_and_validate_encryption', return_value='encrypted')
    @mock.patch(f'{CMD}.Minio.fget_object')
    @mock.patch(f'{CMD}.Minio.list_objects', return_value=[make_object('pdf_1.pdf'), make_object('backup.sqlite3')])
    @mock.patch(f'{CMD}.Minio.bucket_exists', return_value=True)
    def test_preflight_builds_plan(
        self, mock_bucket_exists, mock_list_objects, mock_fget_object, mock_detect, mock_exists
    ):
        plan = Command().preflight(b'key')

        self.assertEqual(plan.db_object, 'backup.sqlite3')
        self.assertEqual(plan.media_objects, ['pdf_1.pdf'])
        self.assertEqual(plan.encryption_mode, 'encrypted')
        self.assertEqual(plan.conflicts, [])
        # the database backup is used as the sample object for encryption validation
        mock_detect.assert_called_once()
        mock_fget_object.assert_called_once()

    @mock.patch(f'{CMD}.Path.exists', return_value=True)
    @mock.patch(f'{CMD}.detect_and_validate_encryption', return_value='plaintext')
    @mock.patch(f'{CMD}.Minio.fget_object')
    @mock.patch(f'{CMD}.Minio.list_objects', return_value=[make_object('pdf_1.pdf'), make_object('backup.sqlite3')])
    @mock.patch(f'{CMD}.Minio.bucket_exists', return_value=True)
    def test_preflight_detects_conflicts(
        self, mock_bucket_exists, mock_list_objects, mock_fget_object, mock_detect, mock_exists
    ):
        plan = Command().preflight(None)

        self.assertEqual(plan.conflicts, [Path(settings.MEDIA_ROOT) / 'pdf_1.pdf'])

    @mock.patch(f'{CMD}.detect_and_validate_encryption', return_value='plaintext')
    @mock.patch(f'{CMD}.Minio.fget_object')
    def test_validate_encryption_downloads_sample(self, mock_fget_object, mock_detect):
        result = Command.validate_encryption('pdf_1.pdf', None)

        sample_path = Path(recover_data.__file__).parent / 'tmp_preflight_sample'
        self.assertEqual(result, 'plaintext')
        mock_fget_object.assert_called_with('pdfding', 'pdf_1.pdf', str(sample_path))
        mock_detect.assert_called_with(sample_path, None)


class TestRecoverDataStage(TestCase):
    @mock.patch.object(Command, 'get_file_from_minio')
    @mock.patch(f'{CMD}.verify_sqlite_file', return_value=True)
    @mock.patch(f'{CMD}.shutil.rmtree')
    @mock.patch(f'{CMD}.Path.mkdir')
    @mock.patch(f'{CMD}.Path.exists', return_value=False)
    def test_stage_downloads_media_and_db(self, mock_exists, mock_mkdir, mock_rmtree, mock_verify, mock_get_file):
        plan = make_plan(media_objects=['a.pdf', 'b.pdf'], db_object='backup.sqlite3')
        staging_dir = Path('/tmp/staging')

        staging_media, staged_db = Command().stage(plan, staging_dir, b'key')

        self.assertEqual(staging_media, staging_dir / 'media')
        self.assertEqual(staged_db, staging_dir / 'backup.sqlite3')
        self.assertEqual(mock_get_file.call_count, 3)
        mock_get_file.assert_any_call('a.pdf', staging_dir / 'media', b'key')
        mock_get_file.assert_any_call('b.pdf', staging_dir / 'media', b'key')
        mock_get_file.assert_any_call('backup.sqlite3', staging_dir, b'key')

    @mock.patch.object(Command, 'get_file_from_minio')
    @mock.patch(f'{CMD}.verify_sqlite_file', return_value=False)
    @mock.patch(f'{CMD}.shutil.rmtree')
    @mock.patch(f'{CMD}.Path.mkdir')
    @mock.patch(f'{CMD}.Path.exists', return_value=False)
    def test_stage_rejects_invalid_database(self, mock_exists, mock_mkdir, mock_rmtree, mock_verify, mock_get_file):
        plan = make_plan(media_objects=[], db_object='backup.sqlite3')

        with self.assertRaises(RecoveryPreflightError):
            Command().stage(plan, Path('/tmp/staging'), b'key')


class TestRecoverDataCommit(TestCase):
    @mock.patch(f'{CMD}.os.replace')
    @mock.patch(f'{CMD}.Path.unlink')
    @mock.patch(f'{CMD}.Path.mkdir')
    @mock.patch(f'{CMD}.Path.is_file', return_value=True)
    @mock.patch(f'{CMD}.Path.rglob')
    @mock.patch(f'{CMD}.Path.exists', return_value=True)
    def test_commit_moves_media_then_switches_db(
        self, mock_exists, mock_rglob, mock_is_file, mock_mkdir, mock_unlink, mock_replace
    ):
        staging_media = Path('/tmp/staging/media')
        mock_rglob.return_value = [staging_media / 'a.pdf', staging_media / 'sub' / 'b.pdf']

        Command.commit(staging_media, Path('/tmp/staging/backup.sqlite3'))

        # 2 media moves + (live -> pre_recovery) + (staged -> live)
        self.assertEqual(mock_replace.call_count, 4)
        media_root = Path(settings.MEDIA_ROOT)
        mock_replace.assert_any_call(staging_media / 'a.pdf', media_root / 'a.pdf')
        mock_replace.assert_any_call(staging_media / 'sub' / 'b.pdf', media_root / 'sub' / 'b.pdf')
        # the saved-aside database is dropped on success
        mock_unlink.assert_called_once()

    @mock.patch(f'{CMD}.os.replace', side_effect=[None, OSError('disk full'), None])
    @mock.patch(f'{CMD}.Path.unlink')
    @mock.patch(f'{CMD}.Path.mkdir')
    @mock.patch(f'{CMD}.Path.rglob', return_value=[])
    @mock.patch(f'{CMD}.Path.exists', return_value=True)
    def test_commit_rolls_back_database_on_failure(
        self, mock_exists, mock_rglob, mock_mkdir, mock_unlink, mock_replace
    ):
        live_db = Path(settings.DATABASES['default']['NAME'])
        pre_recovery_db = live_db.with_name(f'{live_db.name}.pre_recovery')

        with self.assertRaises(OSError):
            Command.commit(Path('/tmp/staging/media'), Path('/tmp/staging/backup.sqlite3'))

        # live -> pre_recovery, failed staged -> live, then rollback pre_recovery -> live
        self.assertEqual(mock_replace.call_count, 3)
        self.assertEqual(mock_replace.call_args_list[2], mock.call(pre_recovery_db, live_db))
        mock_unlink.assert_not_called()


class TestGetFileFromMinio(TestCase):
    @mock.patch('backup.management.commands.recover_data.Minio.fget_object')
    def test_get_file_from_minio_no_encryption(self, mock_fget_object):
        Command.get_file_from_minio('file_name', Path('path'), None)

        mock_fget_object.assert_called_with('pdfding', 'file_name', 'path/file_name')

    @mock.patch('backup.tasks.Path.unlink')
    @mock.patch('backup.management.commands.recover_data.decrypt_file')
    @mock.patch('backup.management.commands.recover_data.Minio.fget_object')
    def test_get_file_from_minio_with_encryption(self, mock_fput_object, mock_decrypt_file, mock_unlink):
        Command.get_file_from_minio('file_name', Path('path'), b'key')

        tmp_file_path = Path(__file__).parents[1] / 'management' / 'commands' / 'tmp_encrypted'

        mock_fput_object.assert_called_with('pdfding', 'file_name', str(tmp_file_path))
        mock_decrypt_file.assert_called_with(b'key', tmp_file_path, Path('path/file_name'))
        mock_unlink.assert_called_with()
