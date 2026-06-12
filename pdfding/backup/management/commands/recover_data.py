import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from backup.service import (
    RecoveryPreflightError,
    decrypt_file,
    detect_and_validate_encryption,
    get_encryption_key,
    verify_sqlite_file,
)
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

# name of the staging directory created (next to the live data) while files are being recovered
STAGING_DIR_NAME = 'recovery_staging'


@dataclass
class RecoveryPlan:
    """Result of the pre-recovery checks, describing what the recovery is going to do."""

    object_names: list[str]  # every object found in the backup bucket
    media_objects: list[str]  # objects that will be recovered into MEDIA_ROOT
    db_object: str | None  # object name of the sqlite db backup to restore, or None
    encryption_mode: str  # detected backup format: 'encrypted' or 'plaintext'
    conflicts: list[Path]  # existing files in the target that the recovery would overwrite


class Command(BaseCommand):
    help = "Recover data from S3 backup"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only run the pre-recovery checks (manifest, encryption config, target conflicts) '
            'and report the result. No data is modified.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Proceed even when existing files in the recovery target would be overwritten.',
        )
        parser.add_argument(
            '--no-input',
            '--noinput',
            action='store_false',
            dest='interactive',
            help='Do not prompt for any confirmation. Implies --force.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        interactive = options['interactive']
        # without an interactive terminal we cannot ask for a second confirmation, so non-interactive
        # runs have to opt into overwriting existing files explicitly via --no-input / --force.
        force = options['force'] or not interactive

        logger.info('----------------------------------------------------')

        if interactive and not dry_run:
            logger.info('Are you sure you want to proceed with the data recovery?')
            logger.info('If you are using a sqlite DB, this operation will overwrite your local DB!')
            logger.info('Type "y" to confirm')
            if input() != 'y':
                self.abort()
                return

        # get the encryption key. if backup encryption is disabled, the result will be None
        encryption_key = get_encryption_key(
            settings.BACKUP_ENCRYPTION_ENABLED, settings.BACKUP_ENCRYPTION_PASSWORD, settings.BACKUP_ENCRYPTION_SALT
        )

        # phase 1: pre-recovery checks. nothing is modified yet.
        logger.info('Running pre-recovery checks')
        try:
            plan = self.preflight(encryption_key)
        except RecoveryPreflightError as error:
            raise CommandError(f'Pre-recovery check failed: {error}')

        self.report_plan(plan)

        if dry_run:
            logger.info('Dry run requested: pre-recovery checks passed and no data was modified.')
            logger.info('----------------------------------------------------')
            return

        if plan.conflicts and not force:
            logger.info(f'{len(plan.conflicts)} existing file(s) in the recovery target will be overwritten.')
            logger.info('Type "y" to confirm overwriting them')
            if input() != 'y':
                self.abort()
                return

        # phase 2: stage everything into a separate directory. all downloads and decryptions happen
        # here, so a failure (e.g. a flaky connection) leaves the live data untouched.
        staging_dir = Path(settings.DATA_DIR) / STAGING_DIR_NAME
        logger.info('Staging recovery files')
        try:
            staging_media, staged_db = self.stage(plan, staging_dir, encryption_key)
        except Exception as error:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise CommandError(f'Staging failed, no data was modified: {error}')

        # phase 3: safely switch to the staged result. these are fast local moves as all the risky
        # work already happened during staging.
        logger.info('Switching to recovered data')
        try:
            self.commit(staging_media, staged_db)
        except Exception as error:
            raise CommandError(f'Failed while switching to recovered data: {error}')
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

        logger.info('Data recovery completed successfully.')
        logger.info('----------------------------------------------------')

    @staticmethod
    def abort():
        logger.info('Aborting data recovery.')
        logger.info('----------------------------------------------------')

    def preflight(self, encryption_key: bytes | None) -> RecoveryPlan:
        """
        Validate the backup before anything is touched: the backup manifest (bucket reachable and not
        empty, sqlite db backup present when needed), the encryption configuration (matches the actual
        backup content) and existing files in the target that would be overwritten.

        Returns a RecoveryPlan on success and raises a RecoveryPreflightError otherwise.
        """

        bucket = settings.BACKUP_BUCKET_NAME

        if not minio_client.bucket_exists(bucket):
            raise RecoveryPreflightError(f'Backup bucket "{bucket}" does not exist on the configured S3 endpoint.')

        object_names = [obj.object_name for obj in minio_client.list_objects(bucket, recursive=True)]
        if not object_names:
            raise RecoveryPreflightError(f'Backup bucket "{bucket}" is empty, there is nothing to recover.')

        db_backup_name = settings.DATABASES['default']['BACKUP_NAME'].name
        is_sqlite = settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
        db_present = db_backup_name in object_names

        if is_sqlite and not db_present:
            raise RecoveryPreflightError(
                f'A sqlite database is configured, but the database backup "{db_backup_name}" is missing from the '
                f'bucket. Recovering files without the database would leave the system in an inconsistent state.'
            )

        db_object = db_backup_name if (is_sqlite and db_present) else None
        media_objects = [name for name in object_names if name != db_backup_name]

        # validate that the configured encryption settings match the actual backup content
        sample_object = db_object or (media_objects[0] if media_objects else object_names[0])
        encryption_mode = self.validate_encryption(sample_object, encryption_key)

        # detect existing files that the recovery would overwrite
        media_root = Path(settings.MEDIA_ROOT)
        conflicts = [media_root / name for name in media_objects if (media_root / name).exists()]

        return RecoveryPlan(object_names, media_objects, db_object, encryption_mode, conflicts)

    @staticmethod
    def validate_encryption(sample_object: str, encryption_key: bytes | None) -> str:
        """
        Download a single backup object and use it to validate the configured encryption settings.
        Returns the detected backup format ('encrypted'/'plaintext') or raises a
        RecoveryPreflightError on a mismatch.
        """

        sample_path = Path(__file__).parent / 'tmp_preflight_sample'
        try:
            minio_client.fget_object(settings.BACKUP_BUCKET_NAME, sample_object, str(sample_path))
            return detect_and_validate_encryption(sample_path, encryption_key)
        finally:
            sample_path.unlink(missing_ok=True)

    @staticmethod
    def report_plan(plan: RecoveryPlan):
        logger.info(f'Found {len(plan.object_names)} object(s) in the backup bucket.')
        logger.info(f'Detected backup format: {plan.encryption_mode}.')
        if plan.db_object:
            logger.info(f'Database backup to restore: {plan.db_object}.')
        else:
            logger.info('No sqlite database backup will be restored (non-sqlite database engine).')
        logger.info(f'Files to recover: {len(plan.media_objects)}.')
        if plan.conflicts:
            logger.info(f'{len(plan.conflicts)} existing file(s) in the target will be overwritten, e.g.:')
            for path in plan.conflicts[:5]:
                logger.info(f'  - {path}')

    def stage(self, plan: RecoveryPlan, staging_dir: Path, encryption_key: bytes | None) -> tuple[Path, Path | None]:
        """
        Download (and, if needed, decrypt) every object into a fresh staging directory. The recovered
        database is verified to be a valid sqlite file before the switch happens. Raises on any failure
        so the caller can clean up without having modified the live data.
        """

        if staging_dir.exists():
            shutil.rmtree(staging_dir)

        staging_media = staging_dir / 'media'
        staging_media.mkdir(parents=True)

        for i, name in enumerate(plan.media_objects):
            (staging_media / name).parent.mkdir(parents=True, exist_ok=True)
            self.get_file_from_minio(name, staging_media, encryption_key)

            if (i + 1) % 10 == 0:  # pragma: no cover
                logger.info(f'Staged {i + 1} / {len(plan.media_objects)} files')

        staged_db = None
        if plan.db_object:
            self.get_file_from_minio(plan.db_object, staging_dir, encryption_key)
            staged_db = staging_dir / plan.db_object
            if not verify_sqlite_file(staged_db):
                raise RecoveryPreflightError(
                    'The recovered database is not a valid sqlite file. This usually points to a wrong '
                    'encryption configuration. Aborting before any data was modified.'
                )

        return staging_media, staged_db

    @staticmethod
    def commit(staging_media: Path, staged_db: Path | None):
        """
        Switch the live data over to the staged result. Media files are moved into place first; the
        database is switched last using an atomic replace with a rollback so a failure cannot leave a
        half-written database behind.
        """

        media_root = Path(settings.MEDIA_ROOT)
        for staged_file in sorted(path for path in staging_media.rglob('*') if path.is_file()):
            target = media_root / staged_file.relative_to(staging_media)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_file, target)

        if staged_db:
            live_db = Path(settings.DATABASES['default']['NAME'])
            live_db.parent.mkdir(parents=True, exist_ok=True)
            pre_recovery_db = live_db.with_name(f'{live_db.name}.pre_recovery')

            had_live_db = live_db.exists()
            if had_live_db:
                # keep the current database aside so it can be restored if the switch fails
                os.replace(live_db, pre_recovery_db)

            try:
                os.replace(staged_db, live_db)
            except Exception:
                if had_live_db:
                    os.replace(pre_recovery_db, live_db)
                raise

            if had_live_db:
                pre_recovery_db.unlink()

    @staticmethod
    def get_file_from_minio(obj_name: str, target_parent_path: Path, encryption_key: bytes):
        """
        Get a file to minio. If an encryption key is provided the file will be decrypted beforehand using cryptography's
        fernet algorithm.
        """

        if encryption_key:
            # get encrypted file from minio, decrypt it and delete the local tmp file
            encrypted_file_path = Path(__file__).parent / 'tmp_encrypted'
            minio_client.fget_object(settings.BACKUP_BUCKET_NAME, obj_name, str(encrypted_file_path))
            decrypt_file(encryption_key, encrypted_file_path, target_parent_path / obj_name)
            encrypted_file_path.unlink()
        else:
            minio_client.fget_object(settings.BACKUP_BUCKET_NAME, obj_name, str(target_parent_path / obj_name))
