import logging
import tempfile
from pathlib import Path

from backup.restore import (
    apply_staged_restore,
    build_manifest,
    cleanup_staging,
    create_safety_backup,
    download_to_staging,
    run_pre_check,
)
from backup.service import get_encryption_key
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from minio import Minio

minio_client = Minio(
    endpoint=settings.BACKUP_ENDPOINT,
    region=settings.BACKUP_REGION,
    secure=settings.BACKUP_SECURE,
    access_key=settings.BACKUP_ACCESS_KEY,
    secret_key=settings.BACKUP_SECRET_KEY,
)

logger = logging.getLogger('management')


class Command(BaseCommand):
    help = 'Recover data from S3 backup with pre-check validation and staged restoration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run pre-check only, do not perform actual restore',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt and proceed directly',
        )
        parser.add_argument(
            '--skip-pre-check',
            action='store_true',
            help='Skip pre-check validation (not recommended)',
        )

    def handle(self, *args, **kwargs):
        dry_run = kwargs['dry_run']
        force = kwargs['force']
        skip_pre_check = kwargs['skip_pre_check']

        logger.info('----------------------------------------------------')

        # --- User Confirmation (backward compatible) ---
        if not force:
            logger.info('Are you sure you want to proceed with the data recovery?')
            logger.info('If you are using a sqlite DB, this operation will overwrite your local DB!')
            logger.info('Type "y" to confirm')
            user_input = input()

            if user_input != 'y':
                logger.info('Aborting data recovery.')
                logger.info('----------------------------------------------------')
                return

        logger.info('Starting data recovery')

        # --- Get encryption key ---
        encryption_key = get_encryption_key(
            settings.BACKUP_ENCRYPTION_ENABLED, settings.BACKUP_ENCRYPTION_PASSWORD, settings.BACKUP_ENCRYPTION_SALT
        )

        # --- Determine paths ---
        is_sqlite = settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
        db_live_path = settings.DATABASES['default']['NAME']
        db_backup_name = settings.DATABASES['default'].get('BACKUP_NAME')
        db_backup_obj_name = db_backup_name.name if db_backup_name else None
        media_root = settings.MEDIA_ROOT

        # --- Pre-check ---
        if not skip_pre_check:
            logger.info('Running pre-check validation...')

            result = run_pre_check(
                minio_client=minio_client,
                bucket_name=settings.BACKUP_BUCKET_NAME,
                db_backup_name=db_backup_obj_name,
                db_live_path=db_live_path,
                media_root=media_root,
                encryption_key=encryption_key,
            )

            # Print results
            for warning in result.warnings:
                logger.warning(warning)
            for error in result.errors:
                logger.error(error)

            if result.manifest:
                logger.info(
                    f'Backup inventory: {result.manifest.object_count} objects, '
                    f'{result.manifest.total_size_bytes} bytes'
                )
                if result.manifest.db_object_name:
                    logger.info(f'  Database: {result.manifest.db_object_name}')
                logger.info(f'  Media files: {len(result.manifest.media_object_names)}')

            if not result.ok:
                raise CommandError('Pre-check failed. Fix the errors above or use --skip-pre-check to override.')

            logger.info('Pre-check passed')

            if dry_run:
                logger.info('Dry run complete — no changes made.')
                logger.info('----------------------------------------------------')
                return
        else:
            logger.warning('Skipping pre-check validation (--skip-pre-check)')

        # --- Safety Backup ---
        if is_sqlite:
            safety_path = db_live_path.with_suffix('.safety_backup')
            if create_safety_backup(db_live_path, safety_path):
                logger.info(f'Created safety backup at {safety_path}')

        # --- Build manifest if pre-check was skipped ---
        if skip_pre_check:
            manifest = build_manifest(minio_client, settings.BACKUP_BUCKET_NAME, db_backup_obj_name)
        else:
            manifest = result.manifest

        # --- Staged Restore ---
        staging_dir = Path(tempfile.mkdtemp(prefix='pdfding_restore_', dir=settings.DATA_DIR))
        logger.info(f'Downloading backup to staging area: {staging_dir}')

        try:
            download_to_staging(
                minio_client, settings.BACKUP_BUCKET_NAME, manifest, staging_dir, encryption_key
            )
            logger.info('All files downloaded successfully')

            logger.info('Applying restored data...')
            apply_staged_restore(staging_dir, manifest, db_live_path, media_root)

            cleanup_staging(staging_dir)

            # Clean up safety backup on success
            if is_sqlite:
                safety_path = db_live_path.with_suffix('.safety_backup')
                if safety_path.exists():
                    safety_path.unlink()

            logger.info('Data recovery completed successfully.')
            logger.info('----------------------------------------------------')

        except Exception as e:
            logger.error(f'Data recovery failed: {e}')
            logger.error(f'Staging directory preserved at: {staging_dir}')
            logger.error('You can manually inspect staged files or retry.')

            if is_sqlite:
                safety_path = db_live_path.with_suffix('.safety_backup')
                if safety_path.exists():
                    logger.error(f'Safety backup available at: {safety_path}')

            logger.info('----------------------------------------------------')
            raise CommandError(f'Data recovery failed: {e}')
