import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from backup.service import decrypt_file, is_fernet_ciphertext, validate_encryption_key
from cryptography.fernet import InvalidToken

logger = logging.getLogger('management')


@dataclass
class BackupManifest:
    """Inventory of what exists in the MinIO backup bucket."""

    db_object_name: str | None
    media_object_names: list[str]
    total_size_bytes: int
    object_count: int


@dataclass
class ConflictReport:
    """Files at target paths that will be overwritten during restore."""

    db_will_overwrite: bool
    media_conflicts: list[str]
    has_conflicts: bool = field(init=False)

    def __post_init__(self):
        self.has_conflicts = self.db_will_overwrite or len(self.media_conflicts) > 0


@dataclass
class PreCheckResult:
    """Aggregated result of all pre-check validations."""

    minio_reachable: bool
    bucket_exists: bool
    manifest: BackupManifest | None
    encryption_valid: bool
    encryption_needed: bool
    conflict_report: ConflictReport | None
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return (
            self.minio_reachable
            and self.bucket_exists
            and self.manifest is not None
            and self.encryption_valid
            and len(self.errors) == 0
        )


def check_minio_connection(minio_client, bucket_name: str) -> tuple[bool, bool, list[str]]:
    """
    Verify MinIO is reachable and the backup bucket exists.

    Returns: (is_reachable, bucket_exists, errors)
    """

    try:
        exists = minio_client.bucket_exists(bucket_name)
        if exists:
            return True, True, []
        else:
            return True, False, [f"Bucket '{bucket_name}' does not exist"]
    except Exception as e:
        return False, False, [f'Cannot connect to MinIO: {e}']


def build_manifest(minio_client, bucket_name: str, db_backup_name: str | None) -> BackupManifest:
    """
    List all objects in the backup bucket and categorize them into database and media files.

    Raises ValueError if the bucket is empty (no backup data found).
    """

    objects = list(minio_client.list_objects(bucket_name, recursive=True))

    if not objects:
        raise ValueError('Backup bucket is empty — no backup data found')

    db_object = None
    media_objects = []
    total_size = 0

    for obj in objects:
        total_size += obj.size or 0
        if db_backup_name and obj.object_name == db_backup_name:
            db_object = obj.object_name
        else:
            media_objects.append(obj.object_name)

    return BackupManifest(
        db_object_name=db_object,
        media_object_names=media_objects,
        total_size_bytes=total_size,
        object_count=len(objects),
    )


def validate_encryption_against_backup(
    minio_client,
    bucket_name: str,
    sample_object_name: str,
    encryption_key: bytes | None,
) -> tuple[bool, bool, list[str]]:
    """
    Download a sample file from the backup and validate encryption configuration.

    Returns: (encryption_valid, encryption_needed, errors)

    Logic:
    - If encryption_key is None: download the sample file and check if it looks like
      Fernet ciphertext. If it is, encryption is needed but no key was provided → error.
    - If encryption_key is provided: download the sample file and attempt decryption.
      If InvalidToken → wrong password/key → error.
    """

    tmp_path = Path(__file__).parent / 'tmp_precheck_sample'

    try:
        minio_client.fget_object(bucket_name, sample_object_name, str(tmp_path))

        with open(tmp_path, 'rb') as f:
            sample_data = f.read()

        if encryption_key is None:
            # Check if the file looks like Fernet ciphertext
            if is_fernet_ciphertext(sample_data):
                return False, True, [
                    'Backup appears to be encrypted but BACKUP_ENCRYPTION_ENABLED is False. '
                    'Enable encryption and provide the correct password.'
                ]
            else:
                return True, False, []
        else:
            # Try to decrypt the sample
            if validate_encryption_key(encryption_key, sample_data):
                return True, True, []
            else:
                return False, True, [
                    'Decryption failed for sample file. '
                    'The encryption password or salt may be incorrect.'
                ]
    finally:
        tmp_path.unlink(missing_ok=True)


def detect_conflicts(
    manifest: BackupManifest,
    db_live_path: Path,
    media_root: Path,
) -> ConflictReport:
    """Check which existing files would be overwritten during restore."""

    db_will_overwrite = db_live_path.exists() if manifest.db_object_name else False

    media_conflicts = []
    for obj_name in manifest.media_object_names:
        target = media_root / obj_name
        if target.exists():
            media_conflicts.append(obj_name)

    return ConflictReport(db_will_overwrite=db_will_overwrite, media_conflicts=media_conflicts)


def run_pre_check(
    minio_client,
    bucket_name: str,
    db_backup_name: str | None,
    db_live_path: Path,
    media_root: Path,
    encryption_key: bytes | None,
) -> PreCheckResult:
    """Orchestrate all pre-check steps. Single entry point for the management command."""

    errors = []
    warnings = []

    # Step 1: Connectivity
    reachable, bucket_exists, conn_errors = check_minio_connection(minio_client, bucket_name)
    errors.extend(conn_errors)

    if not reachable or not bucket_exists:
        return PreCheckResult(
            minio_reachable=reachable,
            bucket_exists=bucket_exists,
            manifest=None,
            encryption_valid=False,
            encryption_needed=False,
            conflict_report=None,
            errors=errors,
            warnings=warnings,
        )

    # Step 2: Manifest
    try:
        manifest = build_manifest(minio_client, bucket_name, db_backup_name)
    except ValueError as e:
        errors.append(str(e))
        return PreCheckResult(
            minio_reachable=reachable,
            bucket_exists=bucket_exists,
            manifest=None,
            encryption_valid=False,
            encryption_needed=False,
            conflict_report=None,
            errors=errors,
            warnings=warnings,
        )

    # Step 3: Encryption validation
    sample_obj = manifest.db_object_name or (manifest.media_object_names[0] if manifest.media_object_names else None)

    if sample_obj:
        enc_valid, enc_needed, enc_errors = validate_encryption_against_backup(
            minio_client, bucket_name, sample_obj, encryption_key
        )
        errors.extend(enc_errors)

        if enc_needed and encryption_key is None:
            warnings.append(
                'Backup appears to be encrypted but BACKUP_ENCRYPTION_ENABLED is False. '
                'Enable encryption and provide the correct password.'
            )
        elif not enc_needed and encryption_key is not None:
            warnings.append(
                'Encryption is enabled in settings but backup appears unencrypted. '
                'Proceeding without decryption.'
            )
    else:
        enc_valid = True
        enc_needed = False

    # Step 4: Conflicts
    conflicts = detect_conflicts(manifest, db_live_path, media_root)

    if conflicts.has_conflicts:
        n_media = len(conflicts.media_conflicts)
        db_status = 'overwritten' if conflicts.db_will_overwrite else 'created'
        warnings.append(f'{n_media} existing media file(s) will be overwritten. DB will be {db_status}.')

    return PreCheckResult(
        minio_reachable=reachable,
        bucket_exists=bucket_exists,
        manifest=manifest,
        encryption_valid=enc_valid,
        encryption_needed=enc_needed,
        conflict_report=conflicts,
        errors=errors,
        warnings=warnings,
    )


def create_safety_backup(db_live_path: Path, safety_backup_path: Path) -> bool:
    """
    Before touching anything, copy the current live DB to a safety location.
    Returns True if backup was created, False if skipped (no existing DB).
    """

    if not db_live_path.exists():
        return False

    safety_backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_live_path, safety_backup_path)

    return True


def _download_single_file(
    minio_client,
    bucket_name: str,
    obj_name: str,
    target_path: Path,
    encryption_key: bytes | None,
):
    """
    Download a single file from MinIO, optionally decrypting.
    Creates parent directories as needed. On decryption failure, raises ValueError with clear message.
    """

    target_path.parent.mkdir(parents=True, exist_ok=True)

    if encryption_key:
        tmp_path = target_path.with_suffix(target_path.suffix + '.enc_tmp')
        try:
            minio_client.fget_object(bucket_name, obj_name, str(tmp_path))
            try:
                decrypt_file(encryption_key, tmp_path, target_path)
            except InvalidToken:
                raise ValueError(
                    f"Decryption failed for '{obj_name}'. "
                    f'The encryption password or salt may be incorrect.'
                )
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        minio_client.fget_object(bucket_name, obj_name, str(target_path))


def download_to_staging(
    minio_client,
    bucket_name: str,
    manifest: BackupManifest,
    staging_dir: Path,
    encryption_key: bytes | None,
) -> list[str]:
    """
    Download ALL backup files into a staging directory.

    The staging directory structure:
      staging_dir/db/<db_object_name>      — if SQLite
      staging_dir/media/<obj_name>         — for each media object

    Returns list of successfully downloaded object names.
    Raises on any download/decrypt failure.
    """

    staging_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    # Download DB if present
    if manifest.db_object_name:
        db_staging = staging_dir / 'db'
        db_staging.mkdir(exist_ok=True)
        _download_single_file(
            minio_client, bucket_name, manifest.db_object_name, db_staging / manifest.db_object_name, encryption_key
        )
        downloaded.append(manifest.db_object_name)
        logger.info('Staged database backup')

    # Download media files
    media_staging = staging_dir / 'media'
    media_staging.mkdir(exist_ok=True)

    for i, obj_name in enumerate(manifest.media_object_names):
        _download_single_file(minio_client, bucket_name, obj_name, media_staging / obj_name, encryption_key)
        downloaded.append(obj_name)

        if (i + 1) % 10 == 0:
            logger.info(f'Staged {i + 1} / {len(manifest.media_object_names)} files')

    return downloaded


def apply_staged_restore(
    staging_dir: Path,
    manifest: BackupManifest,
    db_live_path: Path,
    media_root: Path,
):
    """
    Switch from current data to staged data.

    Phase 1 — Database (SQLite only):
      1. Rename live DB → .pre_restore_bak (atomic on same filesystem)
      2. Move staged DB → live path
      3. If step 2 fails, rename .bak back → rollback

    Phase 2 — Media files:
      1. For each file in staging, move to MEDIA_ROOT
      2. Track successfully moved files for error reporting
    """

    # Phase 1: Database
    if manifest.db_object_name:
        staged_db = staging_dir / 'db' / manifest.db_object_name
        bak_path = db_live_path.with_suffix('.pre_restore_bak')

        if db_live_path.exists():
            db_live_path.rename(bak_path)
            logger.info('Moved live database to safety backup')

        try:
            shutil.move(str(staged_db), str(db_live_path))
            logger.info('Applied restored database')
        except Exception:
            # Rollback: restore the backup
            if bak_path.exists():
                bak_path.rename(db_live_path)
                logger.error('Database restore failed — rolled back to previous database')
            raise

        # Clean up .bak only after successful switch
        if bak_path.exists():
            bak_path.unlink()

    # Phase 2: Media
    staged_media = staging_dir / 'media'
    moved_files = []

    try:
        for obj_name in manifest.media_object_names:
            src = staged_media / obj_name
            dst = media_root / obj_name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved_files.append(obj_name)
    except Exception:
        logger.error(
            f'Media restore failed after moving {len(moved_files)} files. '
            f'Some files may need manual recovery from staging.'
        )
        raise

    logger.info(f'Applied {len(moved_files)} media files')


def cleanup_staging(staging_dir: Path):
    """Remove the staging directory after successful restore."""

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
        logger.info('Cleaned up staging directory')
