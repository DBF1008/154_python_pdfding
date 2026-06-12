import base64
import binascii
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# the magic header every sqlite database file starts with
SQLITE_HEADER = b'SQLite format 3\x00'


class RecoveryPreflightError(Exception):
    """
    Raised when a pre-recovery validation check fails. The message is meant to be actionable and is
    surfaced to the operator running the recovery so they can fix the problem before any data is
    touched.
    """


def get_encryption_key(encryption_enabled: bool, password: str, salt: str):
    """
    Get the encryption key if encryption is activated, otherwise return None.
    """

    if encryption_enabled:
        return generate_encryption_key(password, salt)
    else:
        return None


def generate_encryption_key(password: str, salt: str) -> bytes:
    """
    Generate the encryption key with PBKDF2 using a user provided password and salt.
    """

    password_b = bytes(password, encoding='utf-8')
    salt_b = bytes(salt, encoding='utf-8')

    kdf = PBKDF2HMAC(
        algorithm=SHA256(),
        length=32,
        salt=salt_b,
        iterations=1000000,
    )

    encryption_key = base64.urlsafe_b64encode(kdf.derive(password_b))

    return encryption_key


def encrypt_file(encryption_key: bytes, source_path: Path, target_path: Path):
    """
    Encrypt the specified file using Fernet. Fernet is a symmetric encryption algorithm provided by the cryptography
    library. The encryption key should be generated using PBKDF2.
    """

    with open(source_path, 'rb') as file:
        unencrypted = file.read()

    f = Fernet(encryption_key)
    encrypted = f.encrypt(unencrypted)

    with open(target_path, 'wb') as encrypted_file:
        encrypted_file.write(encrypted)


def decrypt_file(encryption_key: bytes, source_path: Path, target_path: Path):
    """
    Decrypt the specified file using Fernet. Fernet is a symmetric encryption algorithm provided by the cryptography
    library. The encryption key should be generated using PBKDF2.
    """

    f = Fernet(encryption_key)

    # opening the encrypted file
    with open(source_path, 'rb') as enc_file:
        encrypted = enc_file.read()

    # decrypting the file
    decrypted = f.decrypt(encrypted)

    # create the directory if missing
    target_path.parent.mkdir(exist_ok=True, parents=True)

    # opening the file in write mode and
    # writing the decrypted data
    with open(target_path, 'wb') as dec_file:
        dec_file.write(decrypted)


def looks_like_fernet_token(data: bytes) -> bool:
    """
    Heuristically determine whether the given bytes are a Fernet token, i.e. an encrypted backup
    file produced by encrypt_file. A Fernet token is the URL-safe base64 encoding of a binary
    structure that starts with the version byte 0x80 and is at least 57 bytes long
    (version + timestamp + IV + HMAC).

    This is only used to produce a clearer error message when the configured encryption settings do
    not match the backup. The authoritative check is always an actual decryption attempt.
    """

    try:
        decoded = base64.urlsafe_b64decode(data)
    except (binascii.Error, ValueError):
        return False

    return len(decoded) >= 57 and decoded[0] == 0x80


def detect_and_validate_encryption(sample_path: Path, encryption_key: bytes | None) -> str:
    """
    Validate that the configured encryption settings match the actual backup content by inspecting a
    single, already downloaded backup object located at sample_path.

    Returns 'encrypted' or 'plaintext' describing the detected backup format. Raises a
    RecoveryPreflightError with an actionable message when the configuration does not match the
    backup, i.e. a wrong password/salt, encryption enabled for a plaintext backup, or encryption
    disabled for an encrypted backup. This makes recovery compatible with both encrypted and
    unencrypted backups while refusing to start with a mismatching configuration.
    """

    with open(sample_path, 'rb') as sample_file:
        sample = sample_file.read()

    token_like = looks_like_fernet_token(sample)

    if encryption_key:
        try:
            Fernet(encryption_key).decrypt(sample)
        except InvalidToken:
            if token_like:
                raise RecoveryPreflightError(
                    'Backup decryption failed. The backup is encrypted but the configured '
                    'BACKUP_ENCRYPTION_PASSWORD / BACKUP_ENCRYPTION_SALT do not match it. '
                    'Double-check these settings and try again.'
                )
            raise RecoveryPreflightError(
                'Backup decryption failed. BACKUP_ENCRYPTION_ENABLED is set, but the backup does '
                'not appear to be encrypted. Disable backup encryption to recover this backup.'
            )

        return 'encrypted'

    if token_like:
        raise RecoveryPreflightError(
            'The backup appears to be encrypted, but BACKUP_ENCRYPTION_ENABLED is not set. Enable '
            'backup encryption and provide the original password / salt to recover this backup.'
        )

    return 'plaintext'


def verify_sqlite_file(path: Path) -> bool:
    """
    Return whether the file at the given path starts with the sqlite file header. Used after staging
    a recovered database to make sure decryption produced a valid sqlite file before switching to it.
    """

    with open(path, 'rb') as db_file:
        return db_file.read(len(SQLITE_HEADER)) == SQLITE_HEADER
