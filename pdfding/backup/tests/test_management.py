from unittest import mock

from backup.restore import BackupManifest, ConflictReport, PreCheckResult
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


def _make_pre_check_result(ok=True):
    return PreCheckResult(
        minio_reachable=True,
        bucket_exists=True,
        manifest=BackupManifest(
            db_object_name='backup.sqlite3',
            media_object_names=['pdf_1.pdf'],
            total_size_bytes=3072,
            object_count=2,
        ),
        encryption_valid=True,
        encryption_needed=True,
        conflict_report=ConflictReport(db_will_overwrite=False, media_conflicts=[]),
        errors=[],
        warnings=[],
    )


class TestRecoverData(TestCase):
    @mock.patch('backup.management.commands.recover_data.Path.unlink')
    @mock.patch('backup.management.commands.recover_data.cleanup_staging')
    @mock.patch('backup.management.commands.recover_data.apply_staged_restore')
    @mock.patch(
        'backup.management.commands.recover_data.download_to_staging',
        return_value=['backup.sqlite3', 'pdf_1.pdf'],
    )
    @mock.patch(
        'backup.management.commands.recover_data.tempfile.mkdtemp',
        return_value='/tmp/pdfding_restore_staging',
    )
    @mock.patch('backup.management.commands.recover_data.create_safety_backup', return_value=True)
    @mock.patch('backup.management.commands.recover_data.run_pre_check', return_value=_make_pre_check_result())
    @mock.patch('backup.management.commands.recover_data.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_recover_data(
        self,
        mock_input,
        mock_get_encryption_key,
        mock_run_pre_check,
        mock_create_safety,
        mock_mkdtemp,
        mock_download,
        mock_apply,
        mock_cleanup,
        mock_unlink,
    ):
        call_command('recover_data')

        mock_get_encryption_key.assert_called_with(True, 'password', 'pdfding')
        mock_run_pre_check.assert_called_once()
        mock_create_safety.assert_called_once()
        mock_download.assert_called_once()
        mock_apply.assert_called_once()
        mock_cleanup.assert_called_once()

    @mock.patch('backup.management.commands.recover_data.apply_staged_restore')
    @mock.patch('backup.management.commands.recover_data.download_to_staging')
    @mock.patch('backup.management.commands.recover_data.run_pre_check', return_value=_make_pre_check_result())
    @mock.patch('backup.management.commands.recover_data.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_dry_run(self, mock_input, mock_get_key, mock_pre_check, mock_download, mock_apply):
        call_command('recover_data', '--dry-run')

        mock_pre_check.assert_called_once()
        mock_download.assert_not_called()
        mock_apply.assert_not_called()

    @mock.patch('backup.management.commands.recover_data.Path.unlink')
    @mock.patch('backup.management.commands.recover_data.cleanup_staging')
    @mock.patch('backup.management.commands.recover_data.apply_staged_restore')
    @mock.patch(
        'backup.management.commands.recover_data.download_to_staging',
        return_value=['backup.sqlite3', 'pdf_1.pdf'],
    )
    @mock.patch(
        'backup.management.commands.recover_data.tempfile.mkdtemp',
        return_value='/tmp/pdfding_restore_staging',
    )
    @mock.patch('backup.management.commands.recover_data.create_safety_backup', return_value=True)
    @mock.patch('backup.management.commands.recover_data.run_pre_check', return_value=_make_pre_check_result())
    @mock.patch('backup.management.commands.recover_data.get_encryption_key', return_value=b'key')
    def test_force_skips_prompt(
        self,
        mock_get_key,
        mock_pre_check,
        mock_create_safety,
        mock_mkdtemp,
        mock_download,
        mock_apply,
        mock_cleanup,
        mock_unlink,
    ):
        with mock.patch('builtins.input') as mock_input:
            call_command('recover_data', '--force')
            mock_input.assert_not_called()

    @mock.patch('backup.management.commands.recover_data.Path.unlink')
    @mock.patch('backup.management.commands.recover_data.cleanup_staging')
    @mock.patch('backup.management.commands.recover_data.apply_staged_restore')
    @mock.patch(
        'backup.management.commands.recover_data.download_to_staging',
        return_value=['backup.sqlite3', 'pdf_1.pdf'],
    )
    @mock.patch('backup.management.commands.recover_data.build_manifest')
    @mock.patch(
        'backup.management.commands.recover_data.tempfile.mkdtemp',
        return_value='/tmp/pdfding_restore_staging',
    )
    @mock.patch('backup.management.commands.recover_data.create_safety_backup', return_value=True)
    @mock.patch('backup.management.commands.recover_data.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_skip_pre_check(
        self,
        mock_input,
        mock_get_key,
        mock_create_safety,
        mock_mkdtemp,
        mock_build_manifest,
        mock_download,
        mock_apply,
        mock_cleanup,
        mock_unlink,
    ):
        manifest = BackupManifest(
            db_object_name='backup.sqlite3',
            media_object_names=['pdf_1.pdf'],
            total_size_bytes=3072,
            object_count=2,
        )
        mock_build_manifest.return_value = manifest

        call_command('recover_data', '--skip-pre-check')

        mock_build_manifest.assert_called_once()
        mock_download.assert_called_once()

    @mock.patch('backup.management.commands.recover_data.run_pre_check')
    @mock.patch('backup.management.commands.recover_data.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_pre_check_failure_raises_command_error(self, mock_input, mock_get_key, mock_pre_check):
        failed_result = PreCheckResult(
            minio_reachable=False,
            bucket_exists=False,
            manifest=None,
            encryption_valid=False,
            encryption_needed=False,
            conflict_report=None,
            errors=['Cannot connect to MinIO: timeout'],
            warnings=[],
        )
        mock_pre_check.return_value = failed_result

        with self.assertRaises(CommandError) as ctx:
            call_command('recover_data')

        self.assertIn('Pre-check failed', str(ctx.exception))

    @mock.patch('backup.management.commands.recover_data.create_safety_backup', return_value=False)
    @mock.patch('backup.management.commands.recover_data.run_pre_check', return_value=_make_pre_check_result())
    @mock.patch('backup.management.commands.recover_data.get_encryption_key', return_value=b'key')
    @mock.patch('builtins.input', return_value='y')
    def test_restore_failure_raises_command_error(self, mock_input, mock_get_key, mock_pre_check, mock_safety):
        _cmd = 'backup.management.commands.recover_data'
        with mock.patch(f'{_cmd}.download_to_staging', side_effect=ValueError('download failed')):
            with mock.patch(f'{_cmd}.tempfile.mkdtemp', return_value='/tmp/staging'):
                with self.assertRaises(CommandError) as ctx:
                    call_command('recover_data')

                self.assertIn('Data recovery failed', str(ctx.exception))

    @mock.patch('builtins.input', return_value='n')
    def test_user_aborts(self, mock_input):
        with mock.patch('backup.management.commands.recover_data.run_pre_check') as mock_pre_check:
            call_command('recover_data')
            mock_pre_check.assert_not_called()
